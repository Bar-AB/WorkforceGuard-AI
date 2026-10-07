import uuid
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AuditLog, Finding
from app.llm.provider import LLMMisconfiguredError, LLMUnavailableError
from app.rules.findings import RuleFinding
from app.services import scans
from tests.factories import (
    TUESDAY_8AM,
    insert_company,
    insert_company_with_long_day,
    insert_daily_long_shifts,
    insert_employee,
    insert_overtime_policy,
    insert_worked_shift,
)
from tests.fake_llm import FAKE_MODEL

NO_FALLBACKS = {"llm_unavailable": 0, "llm_misconfigured": 0, "invalid_output": 0}

POLICY_START_8AM = datetime(2025, 1, 1, 8, tzinfo=UTC)


async def _scan(client: AsyncClient, company_id: uuid.UUID) -> dict[str, object]:
    response = await client.post("/api/v1/scans", headers={"X-Company-Id": str(company_id)})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


async def _count(
    conn: AsyncConnection, model: type[Finding] | type[AuditLog], **where: object
) -> int:
    query = select(func.count()).select_from(model).filter_by(**where)
    return (await conn.execute(query)).scalar_one()


async def test_scan_stores_finding_for_overtime_day(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id, _ = await insert_company_with_long_day(rollback_conn)

    body = await _scan(client, company_id)

    assert body["findings_detected"] == 1
    assert body["findings_created"] == 1
    finding = (await rollback_conn.execute(select(Finding).filter_by(company_id=company_id))).one()
    assert finding.rule_id == "overtime_breach"
    assert finding.occurred_on.isoformat() == "2026-01-06"
    assert finding.evidence["limit"] == "daily"


async def test_scan_run_twice_stores_no_duplicate_findings(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id, _ = await insert_company_with_long_day(rollback_conn)

    await _scan(client, company_id)
    second = await _scan(client, company_id)

    assert second["findings_detected"] == 1
    assert second["findings_created"] == 0
    assert await _count(rollback_conn, Finding, company_id=company_id) == 1


async def test_scan_writes_audit_row_per_scan_and_per_new_finding(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id, _ = await insert_company_with_long_day(rollback_conn)

    first = await _scan(client, company_id)
    await _scan(client, company_id)

    assert (
        await _count(rollback_conn, AuditLog, company_id=company_id, action="scan.completed") == 2
    )
    assert (
        await _count(rollback_conn, AuditLog, company_id=company_id, action="finding.created") == 1
    )
    scan_row = (
        await rollback_conn.execute(
            select(AuditLog).filter_by(entity_id=uuid.UUID(str(first["scan_id"])))
        )
    ).one()
    assert scan_row.action == "scan.completed"
    assert scan_row.evidence == {
        "rules": {"overtime_breach": "1"},
        "findings_detected": 1,
        "findings_created": 1,
        "rules_skipped": [],
    }
    created = (
        await rollback_conn.execute(
            select(AuditLog).filter_by(company_id=company_id, action="finding.created")
        )
    ).one()
    assert created.entity_type == "finding"
    assert created.rule_version == "1"
    assert created.evidence["scan_id"] == first["scan_id"]
    explained = (
        await rollback_conn.execute(
            select(AuditLog).filter_by(company_id=company_id, action="finding.explained")
        )
    ).one()
    assert explained.entity_type == "finding"
    assert explained.entity_id == created.entity_id
    assert explained.evidence == {
        "scan_id": first["scan_id"],
        "source": "llm",
        "prompt_version": "explain_finding.v2",
        "fallback_reason": None,
    }
    assert explained.model_name == FAKE_MODEL


async def test_scan_only_touches_its_own_company(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_a, _ = await insert_company_with_long_day(rollback_conn)
    company_b, _ = await insert_company_with_long_day(rollback_conn)

    await _scan(client, company_a)

    assert await _count(rollback_conn, Finding, company_id=company_a) == 1
    assert await _count(rollback_conn, Finding, company_id=company_b) == 0


async def _company_without_overtime_policy(conn: AsyncConnection) -> uuid.UUID:
    company_id = await insert_company(conn)
    employee_id = await insert_employee(conn, company_id)
    await insert_worked_shift(conn, company_id, employee_id, TUESDAY_8AM, hours=8)
    return company_id


def _flag_every_employee_rule(employee_id: uuid.UUID) -> scans.Rule:
    async def run(_: AsyncConnection, __: uuid.UUID) -> list[RuleFinding]:
        return [
            RuleFinding(
                rule_id="always_flags",
                rule_version="1",
                employee_id=employee_id,
                occurred_on=date(2026, 1, 6),
                variant="test",
                severity="low",
                summary="Flagged by a test rule.",
                evidence={},
            )
        ]

    return scans.Rule("1", run)


async def _failing_rule_run(_: AsyncConnection, __: uuid.UUID) -> list[RuleFinding]:
    raise RuntimeError("rule crashed")


async def test_scan_without_overtime_policy_reports_rule_skipped_with_reason(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await _company_without_overtime_policy(rollback_conn)

    body = await _scan(client, company_id)

    skipped = [
        {"rule_id": "overtime_breach", "reason": "No overtime policy is in effect on 2026-01-06."}
    ]
    assert body["rules_skipped"] == skipped
    assert body["findings_detected"] == 0
    scan_row = (
        await rollback_conn.execute(
            select(AuditLog).filter_by(entity_id=uuid.UUID(str(body["scan_id"])))
        )
    ).one()
    assert scan_row.evidence["rules_skipped"] == skipped


async def test_scan_skipped_rule_does_not_stop_other_rules(
    client: AsyncClient, rollback_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    await insert_worked_shift(rollback_conn, company_id, employee_id, TUESDAY_8AM, hours=8)
    monkeypatch.setitem(scans.RULES, "always_flags", _flag_every_employee_rule(employee_id))

    body = await _scan(client, company_id)

    assert body["rules_skipped"] == [
        {"rule_id": "overtime_breach", "reason": "No overtime policy is in effect on 2026-01-06."}
    ]
    assert body["findings_created"] == 1
    assert await _count(rollback_conn, Finding, company_id=company_id, rule_id="always_flags") == 1


async def test_scan_rule_crashing_unexpectedly_is_not_skipped(
    client: AsyncClient, rollback_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_id, _ = await insert_company_with_long_day(rollback_conn)
    monkeypatch.setitem(scans.RULES, "crashes", scans.Rule("1", _failing_rule_run))

    with pytest.raises(RuntimeError, match="rule crashed"):
        await client.post("/api/v1/scans", headers={"X-Company-Id": str(company_id)})


async def test_scan_stores_more_findings_than_one_statement_can_bind(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    await insert_overtime_policy(rollback_conn, company_id)
    await insert_daily_long_shifts(
        rollback_conn, company_id, employee_id, POLICY_START_8AM, days=3700
    )

    body = await _scan(client, company_id)

    assert body["findings_created"] == 4228
    assert await _count(rollback_conn, Finding, company_id=company_id) == 4228


async def test_scan_without_company_header_returns_422(client: AsyncClient) -> None:
    response = await client.post("/api/v1/scans")

    assert response.status_code == 422


async def test_scan_response_reports_explanation_counts(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id, _ = await insert_company_with_long_day(rollback_conn)

    first = await _scan(client, company_id)
    second = await _scan(client, company_id)

    assert (first["findings_explained"], first["explanations_fallback"]) == (1, 0)
    assert (second["findings_explained"], second["explanations_fallback"]) == (0, 0)
    assert first["explanations_fallback_by_reason"] == NO_FALLBACKS


@pytest.mark.parametrize(
    ("fake_llm", "reason"),
    [
        ((LLMUnavailableError("down"), LLMUnavailableError("down")), "llm_unavailable"),
        ((LLMMisconfiguredError("rejected"),), "llm_misconfigured"),
        (("not json", "not json"), "invalid_output"),
    ],
    indirect=["fake_llm"],
    ids=["llm_unavailable", "llm_misconfigured", "invalid_output"],
)
async def test_scan_response_counts_fallbacks_by_reason(
    client: AsyncClient, rollback_conn: AsyncConnection, reason: str
) -> None:
    company_id, _ = await insert_company_with_long_day(rollback_conn)

    body = await _scan(client, company_id)

    assert (body["explanations_fallback"], body["explanations_deferred"]) == (1, 0)
    assert body["explanations_fallback_by_reason"] == {**NO_FALLBACKS, reason: 1}
