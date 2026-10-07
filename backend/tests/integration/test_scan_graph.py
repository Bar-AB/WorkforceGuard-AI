import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.types import Text

from app.db.models import AuditLog, Finding
from app.errors import NotFoundError
from app.graph.scan_graph import run_scan
from app.llm.provider import LLMMisconfiguredError, LLMUnavailableError
from app.security.tenant import TenantContext
from tests.factories import (
    insert_company,
    insert_company_with_long_day,
    insert_employee,
    insert_finding,
)
from tests.fake_llm import FAKE_EXPLANATION, FAKE_MODEL, FakeLLM

RAW_MARKER = "RAW-REPLY-MARKER"


async def _tenant_with_long_day(conn: AsyncConnection) -> TenantContext:
    company_id, _ = await insert_company_with_long_day(conn)
    return TenantContext(company_id=company_id)


async def _company_with_backlog(conn: AsyncConnection, count: int) -> TenantContext:
    company_id = await insert_company(conn)
    employee_id = await insert_employee(conn, company_id)
    for day in range(1, count + 1):
        detected_at = datetime(2026, 1, day, tzinfo=UTC)
        await insert_finding(conn, company_id, employee_id, detected_at=detected_at)
    return TenantContext(company_id=company_id)


async def _findings(conn: AsyncConnection, tenant: TenantContext) -> list[Finding]:
    query = (
        select(Finding)
        .filter_by(company_id=tenant.company_id)
        .order_by(Finding.detected_at, Finding.id)
    )
    return [Finding(**row._mapping) for row in await conn.execute(query)]


async def _explained_audits(conn: AsyncConnection, tenant: TenantContext) -> list[AuditLog]:
    query = select(AuditLog).filter_by(company_id=tenant.company_id, action="finding.explained")
    return [AuditLog(**row._mapping) for row in await conn.execute(query)]


async def test_scan_graph_explains_new_finding(rollback_conn: AsyncConnection) -> None:
    tenant = await _tenant_with_long_day(rollback_conn)

    result = await run_scan(rollback_conn, tenant, FakeLLM(), 10)

    assert (result.findings_created, result.findings_explained) == (1, 1)
    assert (result.explanations_fallback, result.explanations_deferred) == (0, 0)
    [finding] = await _findings(rollback_conn, tenant)
    assert finding.explanation == FAKE_EXPLANATION
    assert finding.explanation_source == "llm"
    assert finding.explanation_prompt_version == "explain_finding.v2"


async def test_scan_graph_respects_explain_limit(rollback_conn: AsyncConnection) -> None:
    tenant = await _company_with_backlog(rollback_conn, 3)
    fake = FakeLLM()

    result = await run_scan(rollback_conn, tenant, fake, 2)

    assert result.findings_explained == 2
    assert len(fake.calls) == 2
    explanations = [finding.explanation for finding in await _findings(rollback_conn, tenant)]
    assert explanations == [FAKE_EXPLANATION, FAKE_EXPLANATION, None]


async def test_scan_graph_explains_backlog_on_next_scan(rollback_conn: AsyncConnection) -> None:
    tenant = await _company_with_backlog(rollback_conn, 3)

    await run_scan(rollback_conn, tenant, FakeLLM(), 2)
    second = await run_scan(rollback_conn, tenant, FakeLLM(), 2)

    assert second.findings_explained == 1
    assert all(f.explanation is not None for f in await _findings(rollback_conn, tenant))


async def test_scan_graph_stores_fallback_not_raw_reply(rollback_conn: AsyncConnection) -> None:
    tenant = await _tenant_with_long_day(rollback_conn)
    fake = FakeLLM(f"{RAW_MARKER} {{", f"{RAW_MARKER} {{")

    result = await run_scan(rollback_conn, tenant, fake, 10)

    assert (result.findings_explained, result.explanations_fallback) == (0, 1)
    for finding in await _findings(rollback_conn, tenant):
        assert finding.explanation_source == "fallback"
        assert RAW_MARKER not in (finding.explanation or "")
    evidence_texts = await rollback_conn.scalars(
        select(cast(AuditLog.evidence, Text)).filter_by(company_id=tenant.company_id)
    )
    assert all(RAW_MARKER not in evidence for evidence in evidence_texts)
    [audit] = await _explained_audits(rollback_conn, tenant)
    assert audit.evidence["fallback_reason"] == "invalid_output"


async def test_scan_graph_provider_down_stores_fallback(rollback_conn: AsyncConnection) -> None:
    tenant = await _tenant_with_long_day(rollback_conn)
    fake = FakeLLM(LLMUnavailableError("down"), LLMUnavailableError("down"))

    result = await run_scan(rollback_conn, tenant, fake, 10)

    assert result.explanations_fallback == 1
    [audit] = await _explained_audits(rollback_conn, tenant)
    assert audit.evidence["fallback_reason"] == "llm_unavailable"
    assert audit.model_name is None


async def test_scan_graph_misconfigured_llm_stores_fallback_after_one_call(
    rollback_conn: AsyncConnection,
) -> None:
    tenant = await _tenant_with_long_day(rollback_conn)
    fake = FakeLLM(LLMMisconfiguredError("rejected"))

    result = await run_scan(rollback_conn, tenant, fake, 10)

    assert result.explanations_fallback == 1
    assert len(fake.calls) == 1
    [finding] = await _findings(rollback_conn, tenant)
    assert finding.explanation_source == "fallback"
    [audit] = await _explained_audits(rollback_conn, tenant)
    assert audit.evidence["fallback_reason"] == "llm_misconfigured"


async def test_scan_graph_provider_down_stops_calling_llm_for_rest_of_scan(
    rollback_conn: AsyncConnection,
) -> None:
    tenant = await _company_with_backlog(rollback_conn, 3)
    fake = FakeLLM(LLMUnavailableError("down"), LLMUnavailableError("down"))

    result = await run_scan(rollback_conn, tenant, fake, 10)

    assert len(fake.calls) == 2
    assert (result.findings_explained, result.explanations_fallback) == (0, 1)
    assert result.explanations_deferred == 2
    assert result.explanations_fallback_by_reason["llm_unavailable"] == 1
    sources = [f.explanation_source for f in await _findings(rollback_conn, tenant)]
    assert sources == ["fallback", None, None]
    audits = await _explained_audits(rollback_conn, tenant)
    assert [audit.evidence["fallback_reason"] for audit in audits] == ["llm_unavailable"]


async def test_scan_graph_misconfigured_llm_stops_calling_llm_for_rest_of_scan(
    rollback_conn: AsyncConnection,
) -> None:
    tenant = await _company_with_backlog(rollback_conn, 3)
    fake = FakeLLM(LLMMisconfiguredError("rejected"))

    result = await run_scan(rollback_conn, tenant, fake, 10)

    assert len(fake.calls) == 1
    assert (result.explanations_fallback, result.explanations_deferred) == (1, 2)
    explanations = [f.explanation for f in await _findings(rollback_conn, tenant)]
    assert explanations[0] is not None
    assert explanations[1:] == [None, None]
    audits = await _explained_audits(rollback_conn, tenant)
    assert [audit.evidence["fallback_reason"] for audit in audits] == ["llm_misconfigured"]


async def test_scan_graph_explains_deferred_findings_on_next_scan(
    rollback_conn: AsyncConnection,
) -> None:
    tenant = await _company_with_backlog(rollback_conn, 3)

    await run_scan(
        rollback_conn, tenant, FakeLLM(LLMUnavailableError("down"), LLMUnavailableError("down")), 10
    )
    second = await run_scan(rollback_conn, tenant, FakeLLM(), 10)

    assert (second.findings_explained, second.explanations_deferred) == (2, 0)
    sources = [f.explanation_source for f in await _findings(rollback_conn, tenant)]
    assert sources == ["fallback", "llm", "llm"]


async def test_scan_graph_invalid_output_keeps_calling_llm_for_next_finding(
    rollback_conn: AsyncConnection,
) -> None:
    tenant = await _company_with_backlog(rollback_conn, 2)
    fake = FakeLLM(f"{RAW_MARKER} {{", f"{RAW_MARKER} {{")

    result = await run_scan(rollback_conn, tenant, fake, 10)

    assert len(fake.calls) == 3
    assert (result.findings_explained, result.explanations_fallback) == (1, 1)
    sources = [f.explanation_source for f in await _findings(rollback_conn, tenant)]
    assert sources == ["fallback", "llm"]


async def test_scan_graph_writes_explained_audit_rows(rollback_conn: AsyncConnection) -> None:
    tenant = await _company_with_backlog(rollback_conn, 2)

    result = await run_scan(rollback_conn, tenant, FakeLLM(), 10)

    audits = await _explained_audits(rollback_conn, tenant)
    finding_ids = {finding.id for finding in await _findings(rollback_conn, tenant)}
    assert {audit.entity_id for audit in audits} == finding_ids
    for audit in audits:
        assert audit.entity_type == "finding"
        assert audit.model_name == FAKE_MODEL
        assert audit.evidence == {
            "scan_id": str(result.scan_id),
            "source": "llm",
            "prompt_version": "explain_finding.v2",
            "fallback_reason": None,
        }


async def test_scan_graph_leaves_other_company_findings_untouched(
    rollback_conn: AsyncConnection,
) -> None:
    tenant = await _company_with_backlog(rollback_conn, 1)
    other = await _company_with_backlog(rollback_conn, 2)

    await run_scan(rollback_conn, tenant, FakeLLM(), 10)

    assert [f.explanation for f in await _findings(rollback_conn, other)] == [None, None]
    assert await _explained_audits(rollback_conn, other) == []


async def test_scan_graph_rescan_does_not_reexplain(rollback_conn: AsyncConnection) -> None:
    tenant = await _tenant_with_long_day(rollback_conn)
    fake = FakeLLM()

    await run_scan(rollback_conn, tenant, fake, 10)
    second = await run_scan(rollback_conn, tenant, fake, 10)

    assert (second.findings_explained, second.explanations_fallback) == (0, 0)
    assert len(fake.calls) == 1
    assert len(await _explained_audits(rollback_conn, tenant)) == 1


async def test_scan_graph_explain_limit_zero_calls_no_llm(rollback_conn: AsyncConnection) -> None:
    tenant = await _tenant_with_long_day(rollback_conn)
    fake = FakeLLM()

    result = await run_scan(rollback_conn, tenant, fake, 0)

    assert (result.findings_created, result.findings_explained) == (1, 0)
    assert fake.calls == []
    assert [f.explanation for f in await _findings(rollback_conn, tenant)] == [None]


async def test_scan_graph_unknown_company_raises_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    fake = FakeLLM()
    tenant = TenantContext(company_id=uuid.uuid4())

    with pytest.raises(NotFoundError):
        await run_scan(rollback_conn, tenant, fake, 10)

    assert fake.calls == []
    count = await rollback_conn.scalar(
        select(func.count()).select_from(AuditLog).filter_by(company_id=tenant.company_id)
    )
    assert count == 0
