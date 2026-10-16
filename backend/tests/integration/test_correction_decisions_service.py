import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import AuditLog, Finding, PayrollRun, ProposedCorrection, Shift
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.security.tenant import TenantContext
from app.services.correction_decisions import (
    FINDING_BUSY_MESSAGE,
    STALE_ROW_MESSAGE,
    approve_correction,
    list_corrections,
    lock_pending_correction,
    reject_correction,
)
from app.services.corrections import CorrectionProposal
from app.services.users import Approver
from tests.db_helpers import wait_until_lock_wait
from tests.factories import (
    FIXED_SHIFT_END,
    ShiftFixCase,
    insert_employee,
    insert_payroll_run,
    insert_pending_correction,
    insert_rival_shift_fix,
    insert_shift_fix_case,
    shift_end_fix,
)

ORIGINAL_SHIFT_END = datetime(2026, 1, 5, 16, tzinfo=UTC)


async def _mark_rejected(conn: AsyncConnection, case: ShiftFixCase) -> None:
    await conn.execute(
        text(
            "UPDATE proposed_corrections SET status = 'rejected', "
            "decided_by_user_id = :u, decided_at = now() WHERE id = :id"
        ),
        {"u": case.admin_id, "id": case.correction_id},
    )


def _admin(case: ShiftFixCase) -> Approver:
    return Approver(user_id=case.admin_id, role="admin")


async def _approve(conn: AsyncConnection, case: ShiftFixCase) -> None:
    locked = await lock_pending_correction(conn, case.tenant, case.correction_id)
    await approve_correction(conn, case.tenant, locked, _admin(case))


async def _shift_end(conn: AsyncConnection, case: ShiftFixCase) -> datetime:
    result = await conn.execute(select(Shift.ends_at).where(Shift.id == case.shift_id))
    return result.scalar_one()


async def _decision_audit(conn: AsyncConnection, case: ShiftFixCase) -> list[tuple[object, ...]]:
    rows = await conn.execute(
        select(AuditLog.action, AuditLog.actor, AuditLog.entity_type, AuditLog.entity_id)
        .where(AuditLog.company_id == case.company_id, AuditLog.action != "correction.proposed")
        .order_by(AuditLog.action)
    )
    return [tuple(row) for row in rows]


async def _set_finding_status(conn: AsyncConnection, case: ShiftFixCase, status: str) -> None:
    await conn.execute(
        text("UPDATE findings SET status = :s WHERE id = :id"), {"s": status, "id": case.finding_id}
    )


async def test_approve_correction_patches_row_and_marks_approved(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    await _approve(rollback_conn, case)

    proposal = (
        await rollback_conn.execute(
            select(ProposedCorrection).where(ProposedCorrection.id == case.correction_id)
        )
    ).one()
    assert await _shift_end(rollback_conn, case) == FIXED_SHIFT_END
    assert (proposal.status, proposal.decided_by_user_id) == ("approved", case.admin_id)
    assert proposal.decided_at is not None


async def test_approve_correction_confirms_open_finding_and_audits(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    await _approve(rollback_conn, case)

    actor = f"user:{case.admin_id}"
    status = await rollback_conn.scalar(select(Finding.status).where(Finding.id == case.finding_id))
    applied_evidence = await rollback_conn.scalar(
        select(AuditLog.evidence).where(AuditLog.action == "correction.applied")
    )
    assert status == "confirmed"
    assert await _decision_audit(rollback_conn, case) == [
        ("correction.applied", actor, "shifts", case.shift_id),
        ("correction.approved", actor, "proposed_correction", case.correction_id),
        ("finding.confirmed", actor, "finding", case.finding_id),
    ]
    assert applied_evidence == {"correction_id": str(case.correction_id), "columns": ["ends_at"]}


async def test_approve_correction_confirmed_finding_writes_no_confirm_audit(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await _set_finding_status(rollback_conn, case, "confirmed")

    await _approve(rollback_conn, case)

    actions = [row[0] for row in await _decision_audit(rollback_conn, case)]
    assert actions == ["correction.applied", "correction.approved"]


async def test_approve_correction_dismissed_finding_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await _set_finding_status(rollback_conn, case, "dismissed")

    with pytest.raises(ConflictError, match="dismissed") as caught:
        await _approve(rollback_conn, case)

    assert str(case.finding_id) not in str(caught.value)
    assert await _shift_end(rollback_conn, case) == ORIGINAL_SHIFT_END


async def test_approve_correction_target_of_other_employee_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    other_employee_id = await insert_employee(rollback_conn, case.company_id)
    await rollback_conn.execute(
        text("UPDATE shifts SET employee_id = :e WHERE id = :id"),
        {"e": other_employee_id, "id": case.shift_id},
    )

    with pytest.raises(ConflictError, match="no longer"):
        await _approve(rollback_conn, case)

    assert await _shift_end(rollback_conn, case) == ORIGINAL_SHIFT_END


async def test_approve_correction_missing_target_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await rollback_conn.execute(text("DELETE FROM shifts WHERE id = :id"), {"id": case.shift_id})

    with pytest.raises(ConflictError, match="no longer"):
        await _approve(rollback_conn, case)


async def test_approve_correction_check_violation_is_conflict_without_values(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn, ends_at=datetime(2026, 1, 5, 7, tzinfo=UTC))

    with pytest.raises(ConflictError, match="break a data rule") as caught:
        await _approve(rollback_conn, case)

    assert "ck_shifts_ends_after_start" in str(caught.value)
    assert "07:00" not in str(caught.value)
    assert await _shift_end(rollback_conn, case) == ORIGINAL_SHIFT_END


async def test_reject_correction_changes_only_status_and_audits(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    locked = await lock_pending_correction(rollback_conn, case.tenant, case.correction_id)

    await reject_correction(rollback_conn, case.tenant, locked, _admin(case))

    proposal_status = await rollback_conn.scalar(
        select(ProposedCorrection.status).where(ProposedCorrection.id == case.correction_id)
    )
    finding_status = await rollback_conn.scalar(
        select(Finding.status).where(Finding.id == case.finding_id)
    )
    assert (proposal_status, finding_status) == ("rejected", "open")
    assert await _shift_end(rollback_conn, case) == ORIGINAL_SHIFT_END
    assert await _decision_audit(rollback_conn, case) == [
        ("correction.rejected", f"user:{case.admin_id}", "proposed_correction", case.correction_id)
    ]


async def test_lock_pending_correction_decided_is_conflict(rollback_conn: AsyncConnection) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await _approve(rollback_conn, case)

    with pytest.raises(ConflictError, match="already approved"):
        await lock_pending_correction(rollback_conn, case.tenant, case.correction_id)


async def test_lock_pending_correction_other_company_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    other = await insert_shift_fix_case(rollback_conn)

    with pytest.raises(NotFoundError):
        await lock_pending_correction(rollback_conn, other.tenant, case.correction_id)


async def test_list_corrections_returns_current_values(rollback_conn: AsyncConnection) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    views = await list_corrections(rollback_conn, case.tenant, case.finding_id)

    assert [(view.id, view.status, view.current) for view in views] == [
        (case.correction_id, "pending", {"ends_at": "2026-01-05T16:00:00Z"})
    ]
    assert views[0].patch == {"ends_at": "2026-01-05T15:00:00Z"}
    assert (views[0].decided_by_user_id, views[0].decided_at) == (None, None)


async def test_list_corrections_lists_newest_first(rollback_conn: AsyncConnection) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await _mark_rejected(rollback_conn, case)
    await rollback_conn.execute(
        text("UPDATE proposed_corrections SET created_at = created_at - interval '1 hour'")
    )
    newer_id = await insert_pending_correction(
        rollback_conn,
        case.company_id,
        shift_end_fix(case.finding_id, case.shift_id, "2026-01-05T14:00:00+00:00"),
    )

    views = await list_corrections(rollback_conn, case.tenant, case.finding_id)

    assert [view.id for view in views] == [newer_id, case.correction_id]


async def test_list_corrections_without_proposals_is_empty(rollback_conn: AsyncConnection) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await rollback_conn.execute(text("DELETE FROM proposed_corrections"))

    assert await list_corrections(rollback_conn, case.tenant, case.finding_id) == []


async def test_list_corrections_missing_target_has_null_current(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await rollback_conn.execute(text("DELETE FROM shifts WHERE id = :id"), {"id": case.shift_id})

    views = await list_corrections(rollback_conn, case.tenant, case.finding_id)

    assert [view.current for view in views] == [None]


async def test_list_corrections_unknown_finding_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    other = await insert_shift_fix_case(rollback_conn)

    with pytest.raises(NotFoundError):
        await list_corrections(rollback_conn, TenantContext(other.company_id), case.finding_id)


async def test_approve_numeric_payroll_patch_applies(rollback_conn: AsyncConnection) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    payroll_id = await insert_payroll_run(
        rollback_conn,
        case.company_id,
        case.employee_id,
        date(2026, 1, 1),
        date(2026, 1, 31),
        regular_hours=Decimal("160"),
        overtime_hours=Decimal("12"),
        gross_pay=Decimal("9000"),
    )
    await _mark_rejected(rollback_conn, case)
    correction_id = await insert_pending_correction(
        rollback_conn,
        case.company_id,
        CorrectionProposal(
            finding_id=case.finding_id,
            target_table="payroll_runs",
            target_id=payroll_id,
            patch={"overtime_hours": 10.5},
            reason="Two overtime hours were counted twice.",
        ),
    )
    locked = await lock_pending_correction(rollback_conn, case.tenant, correction_id)

    await approve_correction(rollback_conn, case.tenant, locked, _admin(case))

    overtime = await rollback_conn.scalar(
        select(PayrollRun.overtime_hours).where(PayrollRun.id == payroll_id)
    )
    assert overtime == Decimal("10.50")


async def test_approve_correction_stored_patch_invalid_is_invalid_input(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await rollback_conn.execute(
        text("UPDATE proposed_corrections SET patch = '{\"ends_at\": 5}'::jsonb")
    )

    with pytest.raises(InvalidInputError, match="ends_at"):
        await _approve(rollback_conn, case)


async def test_approve_correction_finding_locked_by_scan_is_conflict(
    committing_engine: AsyncEngine,
) -> None:
    async with committing_engine.begin() as setup:
        case = await insert_shift_fix_case(setup)

    async with committing_engine.connect() as scan, committing_engine.connect() as decide:
        await scan.begin()
        await scan.execute(
            select(Finding.id).where(Finding.id == case.finding_id).with_for_update(key_share=True)
        )
        await decide.begin()
        with pytest.raises(ConflictError) as caught:
            await _approve(decide, case)
        await decide.rollback()
        await scan.rollback()

    async with committing_engine.connect() as check:
        ends_at = await _shift_end(check, case)
        status = await check.scalar(
            select(ProposedCorrection.status).where(ProposedCorrection.id == case.correction_id)
        )
    assert str(caught.value) == FINDING_BUSY_MESSAGE
    assert (ends_at, status) == (ORIGINAL_SHIFT_END, "pending")


async def _correction_state(conn: AsyncConnection, correction_id: object) -> tuple[object, ...]:
    row = (
        await conn.execute(
            select(ProposedCorrection.status, ProposedCorrection.before).where(
                ProposedCorrection.id == correction_id
            )
        )
    ).one()
    return tuple(row)


async def test_approve_after_row_changed_since_proposal_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    rival_id = await insert_rival_shift_fix(rollback_conn, case, "2026-01-05T14:00:00+00:00")
    await _approve(rollback_conn, case)
    locked = await lock_pending_correction(rollback_conn, case.tenant, rival_id)

    with pytest.raises(ConflictError) as caught:
        await approve_correction(rollback_conn, case.tenant, locked, _admin(case))

    assert str(caught.value) == STALE_ROW_MESSAGE
    assert await _shift_end(rollback_conn, case) == FIXED_SHIFT_END
    assert (await _correction_state(rollback_conn, rival_id))[0] == "pending"


async def test_approve_legacy_proposal_without_before_stores_snapshot(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await rollback_conn.execute(
        text("UPDATE proposed_corrections SET before = NULL WHERE id = :id"),
        {"id": case.correction_id},
    )

    await _approve(rollback_conn, case)

    assert await _correction_state(rollback_conn, case.correction_id) == (
        "approved",
        {"ends_at": "2026-01-05T16:00:00Z"},
    )
    assert await _shift_end(rollback_conn, case) == FIXED_SHIFT_END


async def test_concurrent_rival_approve_waits_for_row_and_sees_it_changed(
    committing_engine: AsyncEngine,
) -> None:
    async with committing_engine.begin() as setup:
        case = await insert_shift_fix_case(setup)
        rival_id = await insert_rival_shift_fix(setup, case, "2026-01-05T14:00:00+00:00")

    async def approve_rival() -> None:
        async with committing_engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            locked = await lock_pending_correction(conn, case.tenant, rival_id)
            await approve_correction(conn, case.tenant, locked, _admin(case))

    async with committing_engine.connect() as first:
        await first.begin()
        await _approve(first, case)
        rival = asyncio.create_task(approve_rival())
        await wait_until_lock_wait(committing_engine)
        await first.commit()
    with pytest.raises(ConflictError) as caught:
        await rival

    async with committing_engine.connect() as check:
        ends_at = await _shift_end(check, case)
        status = await check.scalar(
            select(ProposedCorrection.status).where(ProposedCorrection.id == rival_id)
        )
    assert str(caught.value) == STALE_ROW_MESSAGE
    assert (ends_at, status) == (FIXED_SHIFT_END, "pending")
