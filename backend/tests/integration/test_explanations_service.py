import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import AuditLog, Finding
from app.llm.explain import ExplainResult, FindingFacts
from app.llm.structured import FallbackReason
from app.services.explanations import (
    ExplainedFinding,
    PendingFinding,
    pending_explanations,
    store_explanations,
)
from tests.factories import (
    insert_company,
    insert_employee,
    insert_finding,
    mark_finding_explained,
)

SCAN_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
LLM_RESULT = ExplainResult(
    text="Worked 13 hours, over the 12 hour daily limit.",
    source="llm",
    prompt_label="explain_finding.v1",
    model_name="qwen3:4b",
    fallback_reason=None,
)


def _fallback_result(reason: FallbackReason) -> ExplainResult:
    return ExplainResult(
        text="Too many hours Evidence: none.",
        source="fallback",
        prompt_label="explain_finding.v1",
        model_name=None,
        fallback_reason=reason,
    )


async def _company_with_finding(conn: AsyncConnection) -> tuple[uuid.UUID, uuid.UUID]:
    company_id = await insert_company(conn)
    employee_id = await insert_employee(conn, company_id)
    return company_id, await insert_finding(conn, company_id, employee_id)


async def _finding(conn: AsyncConnection, finding_id: uuid.UUID) -> Finding:
    row = (await conn.execute(select(Finding).filter_by(id=finding_id))).one()
    return Finding(**row._mapping)


async def _explained_audit_rows(conn: AsyncConnection, company_id: uuid.UUID) -> list[AuditLog]:
    query = select(AuditLog).filter_by(company_id=company_id, action="finding.explained")
    return [AuditLog(**row._mapping) for row in await conn.execute(query)]


async def test_pending_explanations_returns_only_own_unexplained_findings(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, pending_id = await _company_with_finding(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    explained_id = await insert_finding(rollback_conn, company_id, employee_id)
    await mark_finding_explained(rollback_conn, explained_id)
    await _company_with_finding(rollback_conn)
    await rollback_conn.execute(
        text("UPDATE findings SET evidence = :e WHERE id = :id"),
        {"e": '{"hours": "13.00", "limit": "daily", "nested": {"n": [1, true]}}', "id": pending_id},
    )

    pending = await pending_explanations(rollback_conn, company_id, limit=10)

    assert pending == [
        PendingFinding(
            finding_id=pending_id,
            facts=FindingFacts(
                rule_id="overtime",
                severity="high",
                occurred_on=date(2026, 1, 6),
                summary="Too many hours",
                evidence={"hours": "13.00", "limit": "daily", "nested": {"n": [1, True]}},
            ),
        )
    ]


async def test_pending_explanations_orders_oldest_first_and_limits(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    ids = {}
    for day in (3, 2, 1):
        detected_at = datetime(2026, 1, day, tzinfo=UTC)
        ids[day] = await insert_finding(
            rollback_conn, company_id, employee_id, detected_at=detected_at
        )

    pending = await pending_explanations(rollback_conn, company_id, limit=2)

    assert [item.finding_id for item in pending] == [ids[1], ids[2]]


async def test_pending_explanations_limit_zero_returns_empty(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, _ = await _company_with_finding(rollback_conn)

    assert await pending_explanations(rollback_conn, company_id, limit=0) == []


@pytest.mark.parametrize(
    "result",
    [
        LLM_RESULT,
        _fallback_result("llm_unavailable"),
        _fallback_result("llm_misconfigured"),
        _fallback_result("invalid_output"),
    ],
    ids=["llm", "llm_unavailable", "llm_misconfigured", "invalid_output"],
)
async def test_store_explanations_sets_columns_and_audits(
    rollback_conn: AsyncConnection, result: ExplainResult
) -> None:
    company_id, finding_id = await _company_with_finding(rollback_conn)

    await store_explanations(
        rollback_conn, company_id, SCAN_ID, [ExplainedFinding(finding_id, result)]
    )

    finding = await _finding(rollback_conn, finding_id)
    assert finding.explanation == result.text
    assert finding.explanation_source == result.source
    assert finding.explanation_prompt_version == "explain_finding.v1"
    [audit] = await _explained_audit_rows(rollback_conn, company_id)
    assert audit.entity_type == "finding"
    assert audit.entity_id == finding_id
    assert audit.model_name == result.model_name
    assert audit.evidence == {
        "scan_id": str(SCAN_ID),
        "source": result.source,
        "prompt_version": "explain_finding.v1",
        "fallback_reason": result.fallback_reason,
    }


async def test_store_explanations_skips_already_explained(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id = await _company_with_finding(rollback_conn)
    await mark_finding_explained(rollback_conn, finding_id, "First explanation.")

    await store_explanations(
        rollback_conn, company_id, SCAN_ID, [ExplainedFinding(finding_id, LLM_RESULT)]
    )

    assert (await _finding(rollback_conn, finding_id)).explanation == "First explanation."
    assert await _explained_audit_rows(rollback_conn, company_id) == []


async def test_store_explanations_ignores_other_company_id(
    rollback_conn: AsyncConnection,
) -> None:
    owner_id, finding_id = await _company_with_finding(rollback_conn)
    other_id = await insert_company(rollback_conn)

    await store_explanations(
        rollback_conn, other_id, SCAN_ID, [ExplainedFinding(finding_id, LLM_RESULT)]
    )

    assert (await _finding(rollback_conn, finding_id)).explanation is None
    assert await _explained_audit_rows(rollback_conn, other_id) == []
    assert await _explained_audit_rows(rollback_conn, owner_id) == []


async def test_store_explanations_with_nothing_writes_nothing(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, _ = await _company_with_finding(rollback_conn)

    await store_explanations(rollback_conn, company_id, SCAN_ID, [])

    assert await _explained_audit_rows(rollback_conn, company_id) == []


async def test_pending_explanations_skips_rows_locked_by_another_scan(
    committing_engine: AsyncEngine,
) -> None:
    async with committing_engine.begin() as conn:
        company_id, finding_id = await _company_with_finding(conn)

    async with committing_engine.begin() as first, committing_engine.begin() as second:
        first_pending = await pending_explanations(first, company_id, limit=10)
        second_pending = await pending_explanations(second, company_id, limit=10)

    assert [item.finding_id for item in first_pending] == [finding_id]
    assert second_pending == []


async def test_pending_lock_lets_a_correction_reference_the_finding(
    committing_engine: AsyncEngine,
) -> None:
    async with committing_engine.begin() as conn:
        company_id, finding_id = await _company_with_finding(conn)

    async with committing_engine.begin() as scan, committing_engine.begin() as agent:
        await pending_explanations(scan, company_id, limit=10)
        await agent.execute(text("SET LOCAL lock_timeout = '2s'"))
        await agent.execute(
            text(
                "INSERT INTO proposed_corrections (company_id, finding_id, target_table, "
                "target_id, patch, reason, proposed_by) "
                "VALUES (:c, :f, 'shifts', gen_random_uuid(), '{}', 'r', 'agent')"
            ),
            {"c": company_id, "f": finding_id},
        )
