import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import ONE_PENDING_CORRECTION_INDEX, AuditLog, Finding, ProposedCorrection
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.security.tenant import TenantContext
from app.services.corrections import (
    PENDING_CORRECTION_MESSAGE,
    CorrectionProposal,
    PendingCorrectionError,
    propose_correction,
    read_target_values,
)
from tests.db_helpers import wait_until_lock_wait
from tests.factories import (
    insert_attendance_event,
    insert_company,
    insert_employee,
    insert_finding,
    insert_payroll_run,
    insert_shift,
    insert_user,
)

START = datetime(2026, 1, 5, 8, tzinfo=UTC)


async def _company_with_finding_and_shift(
    conn: AsyncConnection,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    company_id = await insert_company(conn)
    employee_id = await insert_employee(conn, company_id)
    finding_id = await insert_finding(conn, company_id, employee_id)
    shift_id = await insert_shift(conn, company_id, employee_id, START, 8)
    return company_id, finding_id, shift_id


def _shift_proposal(
    finding_id: uuid.UUID, target_id: uuid.UUID, ends_at: str = "2026-01-05T16:00:00+00:00"
) -> CorrectionProposal:
    return CorrectionProposal(
        finding_id=finding_id,
        target_table="shifts",
        target_id=target_id,
        patch={"ends_at": ends_at},
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


async def test_proposal_stores_before_snapshot_of_patched_columns(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    item = await propose_correction(
        rollback_conn,
        TenantContext(company_id),
        _shift_proposal(finding_id, shift_id, "2026-01-05T15:00:00+00:00"),
        "agent:test",
    )

    await rollback_conn.execute(text("RESET ROLE"))
    stored = (
        await rollback_conn.execute(
            text(
                "SELECT before, judge_supported, judge_reason, judged_by "
                "FROM proposed_corrections WHERE id = :id"
            ),
            {"id": item.id},
        )
    ).one()
    assert stored.before == {"ends_at": "2026-01-05T16:00:00Z"}
    assert (stored.judge_supported, stored.judge_reason, stored.judged_by) == (None, None, None)


async def _stored_before(conn: AsyncConnection, correction_id: uuid.UUID) -> object:
    return await conn.scalar(
        select(ProposedCorrection.before).where(ProposedCorrection.id == correction_id)
    )


async def test_proposal_before_keeps_payroll_amount_as_text(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    finding_id = await insert_finding(rollback_conn, company_id, employee_id)
    payroll_id = await insert_payroll_run(
        rollback_conn,
        company_id,
        employee_id,
        date(2026, 1, 1),
        date(2026, 1, 31),
        regular_hours=Decimal("160"),
        overtime_hours=Decimal("12"),
        gross_pay=Decimal("9000"),
    )
    proposal = CorrectionProposal(
        finding_id=finding_id,
        target_table="payroll_runs",
        target_id=payroll_id,
        patch={"gross_pay": 8500},
        reason="Pay was counted twice.",
    )

    item = await propose_correction(rollback_conn, TenantContext(company_id), proposal, "a")

    assert await _stored_before(rollback_conn, item.id) == {"gross_pay": "9000.00"}


async def test_proposal_before_records_null_text_column(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    finding_id = await insert_finding(rollback_conn, company_id, employee_id)
    event_id = await insert_attendance_event(rollback_conn, company_id, employee_id, START)
    proposal = CorrectionProposal(
        finding_id=finding_id,
        target_table="attendance_events",
        target_id=event_id,
        patch={"note": "Badge reader was down."},
        reason="Explain the manual clock-in.",
    )

    item = await propose_correction(rollback_conn, TenantContext(company_id), proposal, "a")

    assert await _stored_before(rollback_conn, item.id) == {"note": None}


async def test_read_target_values_unknown_column_is_invalid_input(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    finding_id = await insert_finding(rollback_conn, company_id, employee_id)
    shift_id = await insert_shift(rollback_conn, company_id, employee_id, START, 8)
    proposal = CorrectionProposal(
        finding_id=finding_id,
        target_table="shifts",
        target_id=shift_id,
        patch={"no_such_column": 1},
        reason="r",
    )

    with pytest.raises(InvalidInputError, match="can be patched"):
        await read_target_values(rollback_conn, TenantContext(company_id), proposal, employee_id)


@pytest.mark.parametrize(
    "judge_values",
    [
        "judge_supported = true, judge_reason = 'Looks right.'",
        "judge_reason = 'Looks right.', judged_by = 'judge_proposal.v1'",
        "judge_supported = false, judged_by = 'judge_proposal.v1'",
    ],
)
async def test_database_refuses_judge_verdict_that_does_not_match_judged_by(
    rollback_conn: AsyncConnection, judge_values: str
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    item = await propose_correction(
        rollback_conn,
        TenantContext(company_id),
        _shift_proposal(finding_id, shift_id),
        "agent:test",
    )

    with pytest.raises(IntegrityError, match="ck_proposed_corrections_judge_matches_judged_by"):
        async with rollback_conn.begin_nested():
            await rollback_conn.execute(
                text(f"UPDATE proposed_corrections SET {judge_values} WHERE id = :id"),
                {"id": item.id},
            )


async def test_database_accepts_judge_verdict_with_judged_by(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    item = await propose_correction(
        rollback_conn,
        TenantContext(company_id),
        _shift_proposal(finding_id, shift_id),
        "agent:test",
    )

    await rollback_conn.execute(
        text(
            "UPDATE proposed_corrections SET judge_supported = true, "
            "judge_reason = 'Looks right.', judged_by = 'judge_proposal.v1' WHERE id = :id"
        ),
        {"id": item.id},
    )
    await rollback_conn.execute(
        text(
            "UPDATE proposed_corrections SET judge_supported = NULL, judge_reason = NULL "
            "WHERE id = :id"
        ),
        {"id": item.id},
    )

    judged_by = await rollback_conn.scalar(
        text("SELECT judged_by FROM proposed_corrections WHERE id = :id"), {"id": item.id}
    )
    assert judged_by == "judge_proposal.v1"


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


async def test_propose_correction_uses_given_correction_id(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    correction_id = uuid.uuid4()

    item = await propose_correction(
        rollback_conn,
        TenantContext(company_id),
        _shift_proposal(finding_id, shift_id),
        "agent:test",
        correction_id=correction_id,
    )

    assert item.id == correction_id


async def _decide_rejected(
    conn: AsyncConnection, company_id: uuid.UUID, item_id: uuid.UUID
) -> None:
    admin_id = await insert_user(conn, company_id, "admin")
    await conn.execute(
        update(ProposedCorrection)
        .where(ProposedCorrection.id == item_id)
        .values(status="rejected", decided_by_user_id=admin_id, decided_at=datetime.now(UTC))
    )


async def _pending_count(conn: AsyncConnection, finding_id: uuid.UUID) -> int:
    count = await conn.scalar(
        select(func.count())
        .select_from(ProposedCorrection)
        .where(ProposedCorrection.finding_id == finding_id, ProposedCorrection.status == "pending")
    )
    return count or 0


async def test_propose_correction_with_pending_proposal_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    tenant = TenantContext(company_id)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))
    await propose_correction(rollback_conn, tenant, _shift_proposal(finding_id, shift_id), "a")

    with pytest.raises(PendingCorrectionError) as caught:
        await propose_correction(rollback_conn, tenant, _shift_proposal(finding_id, shift_id), "b")

    await rollback_conn.execute(text("RESET ROLE"))
    assert str(caught.value) == PENDING_CORRECTION_MESSAGE
    assert await _pending_count(rollback_conn, finding_id) == 1


async def test_propose_correction_after_decided_proposal_is_allowed(
    rollback_conn: AsyncConnection,
) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    tenant = TenantContext(company_id)
    first = await propose_correction(
        rollback_conn, tenant, _shift_proposal(finding_id, shift_id), "a"
    )
    await _decide_rejected(rollback_conn, company_id, first.id)

    second = await propose_correction(
        rollback_conn, tenant, _shift_proposal(finding_id, shift_id), "b"
    )

    assert (second.status, await _pending_count(rollback_conn, finding_id)) == ("pending", 1)


async def test_database_refuses_second_pending_correction(rollback_conn: AsyncConnection) -> None:
    company_id, finding_id, shift_id = await _company_with_finding_and_shift(rollback_conn)
    row = {"c": company_id, "f": finding_id, "t": shift_id}
    insert_pending = text(
        "INSERT INTO proposed_corrections (company_id, finding_id, target_table, target_id, "
        "patch, reason, proposed_by) VALUES (:c, :f, 'shifts', :t, '{}', 'r', 'agent:test')"
    )
    await rollback_conn.execute(insert_pending, row)

    with pytest.raises(IntegrityError, match=ONE_PENDING_CORRECTION_INDEX):
        async with rollback_conn.begin_nested():
            await rollback_conn.execute(insert_pending, row)


async def test_concurrent_proposals_keep_one_pending(committing_engine: AsyncEngine) -> None:
    async with committing_engine.begin() as setup:
        company_id, finding_id, shift_id = await _company_with_finding_and_shift(setup)
    tenant = TenantContext(company_id)
    proposal = _shift_proposal(finding_id, shift_id)

    async def second_proposal() -> None:
        async with committing_engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            await propose_correction(conn, tenant, proposal, "b")

    async with committing_engine.connect() as first:
        await first.begin()
        await propose_correction(first, tenant, proposal, "a")
        second = asyncio.create_task(second_proposal())
        await wait_until_lock_wait(committing_engine)
        await first.commit()
    with pytest.raises(PendingCorrectionError) as caught:
        await second

    assert str(caught.value) == PENDING_CORRECTION_MESSAGE
    async with committing_engine.connect() as check:
        assert await _pending_count(check, finding_id) == 1
