import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AuditLog, Finding, ProposedCorrection
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.security.tenant import TenantContext
from app.services.corrections import CorrectionProposal, propose_correction
from tests.factories import insert_company, insert_employee, insert_finding, insert_shift

START = datetime(2026, 1, 5, 8, tzinfo=UTC)


async def _company_with_finding_and_shift(
    conn: AsyncConnection,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    company_id = await insert_company(conn)
    employee_id = await insert_employee(conn, company_id)
    finding_id = await insert_finding(conn, company_id, employee_id)
    shift_id = await insert_shift(conn, company_id, employee_id, START, 8)
    return company_id, finding_id, shift_id


def _shift_proposal(finding_id: uuid.UUID, target_id: uuid.UUID) -> CorrectionProposal:
    return CorrectionProposal(
        finding_id=finding_id,
        target_table="shifts",
        target_id=target_id,
        patch={"ends_at": "2026-01-05T16:00:00+00:00"},
        reason="Shift was planned one hour too long.",
    )


async def test_propose_correction_writes_pending_row_and_audit_row(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    item = await propose_correction(
        rollback_conn,
        TenantContext(company_id),
        _shift_proposal(finding_id, shift_id),
        "agent:test",
    )

    await rollback_conn.execute(text("RESET ROLE"))
    stored = (
        await rollback_conn.execute(
            select(ProposedCorrection).where(ProposedCorrection.company_id == company_id)
        )
    ).one()
    audit = (
        await rollback_conn.execute(select(AuditLog).where(AuditLog.company_id == company_id))
    ).one()
    assert (item.id, item.status, item.proposed_by) == (stored.id, "pending", "agent:test")
    assert stored.patch == {"ends_at": "2026-01-05T16:00:00Z"}
    assert (audit.action, audit.entity_type, audit.entity_id, audit.actor) == (
        "correction.proposed",
        "proposed_correction",
        item.id,
        "mcp_reader",
    )
    assert audit.evidence == {
        "finding_id": str(finding_id),
        "target_table": "shifts",
        "target_id": str(shift_id),
    }


async def test_propose_correction_other_company_finding_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, _, shift_id = await _company_with_finding_and_shift(rollback_conn)
    _, other_finding_id, _ = await _company_with_finding_and_shift(rollback_conn)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(NotFoundError, match="Finding"):
        await propose_correction(
            rollback_conn,
            TenantContext(company_id),
            _shift_proposal(other_finding_id, shift_id),
            "agent:test",
        )


async def test_propose_correction_other_company_target_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, _ = await _company_with_finding_and_shift(rollback_conn)
    _, _, other_shift_id = await _company_with_finding_and_shift(rollback_conn)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(NotFoundError, match="shifts"):
        await propose_correction(
            rollback_conn,
            TenantContext(company_id),
            _shift_proposal(finding_id, other_shift_id),
            "agent:test",
        )


async def test_propose_correction_target_in_other_table_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))
    proposal = CorrectionProposal(
        finding_id=finding_id,
        target_table="attendance_events",
        target_id=shift_id,
        patch={"note": "fixed"},
        reason="Wrong table on purpose.",
    )

    with pytest.raises(NotFoundError, match="attendance_events"):
        await propose_correction(rollback_conn, TenantContext(company_id), proposal, "agent:test")


async def test_propose_correction_failure_writes_nothing(rollback_conn: AsyncConnection) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    await rollback_conn.execute(text("REVOKE INSERT ON audit_log FROM mcp_reader"))
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(ProgrammingError, match="permission denied for table audit_log"):
        async with rollback_conn.begin_nested():
            await propose_correction(
                rollback_conn,
                TenantContext(company_id),
                _shift_proposal(finding_id, shift_id),
                "agent:test",
            )

    await rollback_conn.execute(text("RESET ROLE"))
    stored = await rollback_conn.scalars(
        select(ProposedCorrection.id).where(ProposedCorrection.company_id == company_id)
    )
    assert stored.all() == []


async def test_propose_correction_target_of_other_employee_is_refused(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, _ = await _company_with_finding_and_shift(rollback_conn)
    other_employee_id = await insert_employee(rollback_conn, company_id)
    other_shift_id = await insert_shift(rollback_conn, company_id, other_employee_id, START, 8)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(InvalidInputError, match="employee"):
        await propose_correction(
            rollback_conn,
            TenantContext(company_id),
            _shift_proposal(finding_id, other_shift_id),
            "agent:test",
        )


async def test_propose_correction_dismissed_finding_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    await rollback_conn.execute(
        update(Finding).where(Finding.id == finding_id).values(status="dismissed")
    )
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(ConflictError, match="dismissed"):
        await propose_correction(
            rollback_conn,
            TenantContext(company_id),
            _shift_proposal(finding_id, shift_id),
            "agent:test",
        )
