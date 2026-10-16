import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.db.models import AttendanceEvent, AuditLog, Shift
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.security.tenant import TenantContext
from app.services import correction_guard
from app.services.correction_guard import (
    ResolveCheck,
    ResolveResult,
    check_finding_detected,
    check_fix_resolves,
)
from app.services.corrections import CorrectionProposal
from tests.factories import (
    TUESDAY_8AM,
    SuggestCase,
    fix_clock_out_already,
    insert_company,
    insert_employee,
    insert_suggest_case,
    insert_user,
    insert_worked_shift,
    remove_overtime_policy,
    set_finding_facts,
    shift_end_fix,
)

TWELVE_HOUR_CLOCK_OUT: Final = "2026-01-06T20:00:00+00:00"
TWENTY_SECONDS_EARLIER: Final = "2026-01-06T20:59:40+00:00"
ELEVEN_HOUR_CLOCK_OUT: Final = "2026-01-06T19:00:00+00:00"
END_BEFORE_SHIFT_START: Final = "2026-01-06T07:00:00+00:00"
SHIFT_START_DAY_BEFORE: Final = "2026-01-05T08:00:00+00:00"
CLOCK_IN_LATE_DAY_BEFORE: Final = "2026-01-05T23:59:00+00:00"
CLOCK_IN_BEFORE_POLICY: Final = "2024-06-03T08:00:00+00:00"
SUNDAY_MIDNIGHT: Final = datetime(2026, 1, 4, tzinfo=UTC)
MONDAY_MIDNIGHT: Final = datetime(2026, 1, 5, tzinfo=UTC)
MONDAY_8AM: Final = datetime(2026, 1, 5, 8, tzinfo=UTC)
WEDNESDAY_8AM: Final = datetime(2026, 1, 7, 8, tzinfo=UTC)
SECOND_WRITER_BOUND_SECONDS: Final = 5
TEST_GUARD_LOCK_TIMEOUT_MS: Final = 200
REQUEST_LOCK_TIMEOUT: Final = "7s"

Arrange = Callable[[AsyncConnection, SuggestCase], Awaitable[None]]
BuildFix = Callable[[SuggestCase], CorrectionProposal]


def _clock_out_fix(case: SuggestCase, occurred_at: str) -> CorrectionProposal:
    return CorrectionProposal(
        finding_id=case.finding_id,
        target_table="attendance_events",
        target_id=case.clock_out_id,
        patch={"occurred_at": occurred_at},
        reason="The clock-out is late.",
    )


def _clearing_fix(case: SuggestCase) -> CorrectionProposal:
    return _clock_out_fix(case, TWELVE_HOUR_CLOCK_OUT)


def _twenty_second_fix(case: SuggestCase) -> CorrectionProposal:
    return _clock_out_fix(case, TWENTY_SECONDS_EARLIER)


def _constraint_breaking_fix(case: SuggestCase) -> CorrectionProposal:
    return shift_end_fix(case.finding_id, case.shift_id, END_BEFORE_SHIFT_START)


def _clock_in_fix(case: SuggestCase, occurred_at: str) -> CorrectionProposal:
    return _clock_out_fix(case, occurred_at).model_copy(update={"target_id": case.clock_in_id})


def _shift_start_to_day_before_fix(case: SuggestCase) -> CorrectionProposal:
    return shift_end_fix(case.finding_id, case.shift_id, "").model_copy(
        update={"patch": {"starts_at": SHIFT_START_DAY_BEFORE}}
    )


def _clock_in_to_day_before_fix(case: SuggestCase) -> CorrectionProposal:
    return _clock_in_fix(case, CLOCK_IN_LATE_DAY_BEFORE)


def _clock_in_before_policy_fix(case: SuggestCase) -> CorrectionProposal:
    return _clock_in_fix(case, CLOCK_IN_BEFORE_POLICY)


def _clock_out_retyped_fix(case: SuggestCase) -> CorrectionProposal:
    return _clock_out_fix(case, "").model_copy(update={"patch": {"event_type": "clock_in"}})


def _clock_out_before_clock_in_fix(case: SuggestCase) -> CorrectionProposal:
    return _clock_out_fix(case, END_BEFORE_SHIFT_START)


async def _resolve_check(
    conn: AsyncConnection, tenant: TenantContext, fix: CorrectionProposal
) -> ResolveCheck:
    return (await check_fix_resolves(conn, tenant, fix)).check


async def _lock_timeout(conn: AsyncConnection) -> object:
    return await conn.scalar(text("SHOW lock_timeout"))


async def _leave_as_detected(conn: AsyncConnection, case: SuggestCase) -> None:
    return None


async def _set_finding_status(conn: AsyncConnection, case: SuggestCase, status: str) -> None:
    await conn.execute(
        text("UPDATE findings SET status = :s WHERE id = :id"), {"s": status, "id": case.finding_id}
    )


async def _source_and_audit_state(conn: AsyncConnection, case: SuggestCase) -> tuple[object, ...]:
    shift = (
        await conn.execute(
            select(Shift.employee_id, Shift.starts_at, Shift.ends_at).where(
                Shift.id == case.shift_id
            )
        )
    ).one()
    events = (
        await conn.execute(
            select(AttendanceEvent.id, AttendanceEvent.event_type, AttendanceEvent.occurred_at)
            .where(AttendanceEvent.company_id == case.company_id)
            .order_by(AttendanceEvent.occurred_at)
        )
    ).all()
    audits = await conn.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.company_id == case.company_id)
    )
    return tuple(shift), [tuple(event) for event in events], audits


async def test_check_fix_resolves_returns_the_target_values_it_read(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    result = await check_fix_resolves(rollback_conn, case.tenant, _clearing_fix(case))

    assert result == ResolveResult(check="resolves", before={"occurred_at": "2026-01-06T21:00:00Z"})


async def test_check_fix_resolves_true_for_fix_that_clears_overtime(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    check = await _resolve_check(rollback_conn, case.tenant, _clearing_fix(case))

    assert check == "resolves"


async def test_check_fix_resolves_still_detected_for_twenty_second_fix(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    check = await _resolve_check(rollback_conn, case.tenant, _twenty_second_fix(case))

    assert check == "still_detected"


async def test_check_fix_resolves_still_detected_for_shift_end_fix(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    fix = shift_end_fix(case.finding_id, case.shift_id, TWELVE_HOUR_CLOCK_OUT)

    check = await _resolve_check(rollback_conn, case.tenant, fix)

    assert check == "still_detected"


@pytest.mark.parametrize("status", ["open", "confirmed"])
async def test_check_fix_resolves_already_gone_when_data_is_already_fixed(
    rollback_conn: AsyncConnection, status: str
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await fix_clock_out_already(rollback_conn, case)
    await _set_finding_status(rollback_conn, case, status)
    fix = _clock_out_fix(case, ELEVEN_HOUR_CLOCK_OUT)

    check = await _resolve_check(rollback_conn, case.tenant, fix)

    assert check == "already_gone"


async def test_check_fix_resolves_already_gone_does_not_try_the_patch(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await fix_clock_out_already(rollback_conn, case)

    check = await _resolve_check(rollback_conn, case.tenant, _constraint_breaking_fix(case))

    assert check == "already_gone"


async def test_check_fix_resolves_unchecked_without_policy(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await remove_overtime_policy(rollback_conn, case)

    check = await _resolve_check(rollback_conn, case.tenant, _clearing_fix(case))

    assert check == "unchecked"


async def test_check_fix_resolves_unchecked_for_unknown_rule(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await set_finding_facts(rollback_conn, case.finding_id, rule_id="retired_rule")

    check = await _resolve_check(rollback_conn, case.tenant, _clearing_fix(case))

    assert check == "unchecked"


async def test_check_fix_resolves_invalid_patch_when_patch_breaks_constraint(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)

    with caplog.at_level(logging.WARNING):
        check = await _resolve_check(rollback_conn, case.tenant, _constraint_breaking_fix(case))

    messages = [record.getMessage() for record in caplog.records]
    assert check == "invalid_patch"
    assert [m for m in messages if "ck_shifts_ends_after_start" in m] != []
    assert [m for m in messages if str(case.shift_id) in m or "07:00" in m] == []


@pytest.mark.parametrize("build_fix", [_shift_start_to_day_before_fix, _clock_in_to_day_before_fix])
async def test_check_fix_resolves_moves_problem_when_fix_moves_the_breach_to_another_day(
    rollback_conn: AsyncConnection, build_fix: BuildFix
) -> None:
    case = await insert_suggest_case(rollback_conn)

    check = await _resolve_check(rollback_conn, case.tenant, build_fix(case))

    assert check == "moves_problem"


@pytest.mark.parametrize("build_fix", [_shift_start_to_day_before_fix, _clock_in_to_day_before_fix])
async def test_check_fix_resolves_moves_problem_when_hours_land_on_a_day_already_over_the_limit(
    rollback_conn: AsyncConnection, build_fix: BuildFix
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await insert_worked_shift(
        rollback_conn, case.company_id, case.employee_id, MONDAY_8AM, hours=13
    )

    check = await _resolve_check(rollback_conn, case.tenant, build_fix(case))

    assert check == "moves_problem"


async def test_check_fix_resolves_moves_problem_when_weekly_crossing_moves_to_a_later_day(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    for day_start in (SUNDAY_MIDNIGHT, MONDAY_MIDNIGHT):
        await insert_worked_shift(
            rollback_conn, case.company_id, case.employee_id, day_start, hours=23
        )
    await insert_worked_shift(
        rollback_conn, case.company_id, case.employee_id, WEDNESDAY_8AM, hours=2
    )

    check = await _resolve_check(rollback_conn, case.tenant, _clearing_fix(case))

    assert check == "moves_problem"


async def test_check_fix_resolves_logs_the_cause_without_ids(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)

    with caplog.at_level(logging.WARNING):
        check = await _resolve_check(rollback_conn, case.tenant, _twenty_second_fix(case))

    messages = [record.getMessage() for record in caplog.records]
    assert check == "still_detected"
    assert [m for m in messages if "target_still_detected=True" in m and "new_or_bigger=0" in m]
    assert [m for m in messages if str(case.employee_id) in m or "2026-01-06" in m] == []


async def test_check_fix_resolves_ignores_breaches_that_were_already_there(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    colleague_id = await insert_employee(rollback_conn, case.company_id)
    await insert_worked_shift(rollback_conn, case.company_id, colleague_id, TUESDAY_8AM, hours=13)

    check = await _resolve_check(rollback_conn, case.tenant, _clearing_fix(case))

    assert check == "resolves"


async def test_known_limit_fix_that_retypes_the_clock_out_counts_as_resolves(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    check = await _resolve_check(rollback_conn, case.tenant, _clock_out_retyped_fix(case))

    assert check == "resolves"


async def test_known_limit_fix_that_puts_clock_out_before_clock_in_counts_as_resolves(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    check = await _resolve_check(rollback_conn, case.tenant, _clock_out_before_clock_in_fix(case))

    assert check == "resolves"


async def test_guard_rolls_back_patch_when_rule_cannot_run_on_patched_data(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    before = await _source_and_audit_state(rollback_conn, case)

    with caplog.at_level(logging.WARNING):
        check = await _resolve_check(rollback_conn, case.tenant, _clock_in_before_policy_fix(case))

    messages = [record.getMessage() for record in caplog.records]
    assert check == "breaks_rule"
    assert await _source_and_audit_state(rollback_conn, case) == before
    assert [m for m in messages if "patched data" in m and "MissingOvertimePolicyError" in m] != []
    assert [m for m in messages if str(case.clock_in_id) in m or "2024-06-03" in m] == []


async def test_guard_lock_wait_bound_does_not_leak_into_the_request_transaction(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await rollback_conn.execute(text(f"SET LOCAL lock_timeout = '{REQUEST_LOCK_TIMEOUT}'"))

    check = await _resolve_check(rollback_conn, case.tenant, _clearing_fix(case))

    assert check == "resolves"
    assert await _lock_timeout(rollback_conn) == REQUEST_LOCK_TIMEOUT


async def test_guard_is_busy_when_another_update_holds_the_row(
    committing_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(correction_guard, "GUARD_LOCK_TIMEOUT_MS", TEST_GUARD_LOCK_TIMEOUT_MS)
    async with committing_engine.begin() as setup:
        case = await insert_suggest_case(setup)
        before = await _source_and_audit_state(setup, case)

    async with committing_engine.connect() as holder, committing_engine.connect() as first:
        await holder.begin()
        await holder.execute(
            text("SELECT id FROM attendance_events WHERE id = :id FOR UPDATE"),
            {"id": case.clock_out_id},
        )
        await first.begin()
        await first.execute(text(f"SET LOCAL lock_timeout = '{REQUEST_LOCK_TIMEOUT}'"))
        async with asyncio.timeout(SECOND_WRITER_BOUND_SECONDS):
            check = await _resolve_check(first, case.tenant, _clearing_fix(case))
        usable_as = await insert_user(first, case.company_id, "manager")
        lock_timeout = await _lock_timeout(first)
        await first.rollback()
        await holder.rollback()

    async with committing_engine.connect() as check_conn:
        after = await _source_and_audit_state(check_conn, case)
    assert check == "busy"
    assert usable_as is not None
    assert lock_timeout == REQUEST_LOCK_TIMEOUT
    assert after == before


@pytest.mark.parametrize(
    "arrange", [_leave_as_detected, fix_clock_out_already, remove_overtime_policy]
)
@pytest.mark.parametrize(
    "build_fix",
    [
        _clearing_fix,
        _twenty_second_fix,
        _constraint_breaking_fix,
        _shift_start_to_day_before_fix,
        _clock_in_to_day_before_fix,
        _clock_in_before_policy_fix,
        _clock_out_retyped_fix,
    ],
)
async def test_guard_leaves_row_unchanged(
    rollback_conn: AsyncConnection, arrange: Arrange, build_fix: BuildFix
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await arrange(rollback_conn, case)
    before = await _source_and_audit_state(rollback_conn, case)

    await _resolve_check(rollback_conn, case.tenant, build_fix(case))

    assert await _source_and_audit_state(rollback_conn, case) == before
    assert before[2] == 0


async def test_guard_releases_row_lock_before_transaction_end(
    committing_engine: AsyncEngine,
) -> None:
    async with committing_engine.begin() as setup:
        case = await insert_suggest_case(setup)

    async with committing_engine.connect() as first:
        await first.begin()
        check = await _resolve_check(first, case.tenant, _clearing_fix(case))
        async with asyncio.timeout(SECOND_WRITER_BOUND_SECONDS):
            async with committing_engine.begin() as second:
                await second.execute(text("SET LOCAL lock_timeout = '1s'"))
                written = await second.execute(
                    text("UPDATE attendance_events SET occurred_at = occurred_at WHERE id = :id"),
                    {"id": case.clock_out_id},
                )
        await first.rollback()

    assert check == "resolves"
    assert written.rowcount == 1


async def test_check_fix_resolves_dismissed_finding_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await _set_finding_status(rollback_conn, case, "dismissed")

    with pytest.raises(ConflictError, match="dismissed"):
        await _resolve_check(rollback_conn, case.tenant, _clearing_fix(case))


async def test_check_fix_resolves_other_company_finding_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    other = TenantContext(await insert_company(rollback_conn))

    with pytest.raises(NotFoundError):
        await _resolve_check(rollback_conn, other, _clearing_fix(case))


async def test_check_fix_resolves_target_of_other_employee_is_invalid_input(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await remove_overtime_policy(rollback_conn, case)
    other_employee_id = await insert_employee(rollback_conn, case.company_id)
    await rollback_conn.execute(
        text("UPDATE attendance_events SET employee_id = :e WHERE id = :id"),
        {"e": other_employee_id, "id": case.clock_out_id},
    )

    with pytest.raises(InvalidInputError, match="not about the finding's employee"):
        await _resolve_check(rollback_conn, case.tenant, _clearing_fix(case))


async def test_check_fix_resolves_locked_column_patch_is_invalid_input(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    fix = _clearing_fix(case).model_copy(update={"patch": {"employee_id": str(case.employee_id)}})

    with pytest.raises(InvalidInputError, match="can be patched"):
        await _resolve_check(rollback_conn, case.tenant, fix)


async def test_check_finding_detected_other_company_finding_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    other = TenantContext(await insert_company(rollback_conn))

    with pytest.raises(NotFoundError):
        await check_finding_detected(rollback_conn, other, case.finding_id)


async def test_check_finding_detected_dismissed_finding_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await _set_finding_status(rollback_conn, case, "dismissed")

    with pytest.raises(ConflictError, match="dismissed"):
        await check_finding_detected(rollback_conn, case.tenant, case.finding_id)


async def test_check_finding_detected_for_finding_the_rule_still_reports(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    check = await check_finding_detected(rollback_conn, case.tenant, case.finding_id)

    assert check == "detected"


async def test_check_finding_detected_already_gone_when_data_is_already_fixed(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await fix_clock_out_already(rollback_conn, case)

    check = await check_finding_detected(rollback_conn, case.tenant, case.finding_id)

    assert check == "already_gone"


async def test_check_finding_detected_unchecked_without_policy_logs_the_cause(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await remove_overtime_policy(rollback_conn, case)

    with caplog.at_level(logging.WARNING):
        check = await check_finding_detected(rollback_conn, case.tenant, case.finding_id)

    messages = [record.getMessage() for record in caplog.records]
    assert check == "unchecked"
    assert [m for m in messages if "MissingOvertimePolicyError" in m] != []
    assert [m for m in messages if str(case.finding_id) in m or "2026-01-06" in m] == []


async def test_check_finding_detected_unchecked_for_unknown_rule_logs_the_cause(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await set_finding_facts(rollback_conn, case.finding_id, rule_id="retired_rule")

    with caplog.at_level(logging.WARNING):
        check = await check_finding_detected(rollback_conn, case.tenant, case.finding_id)

    assert check == "unchecked"
    assert [r.getMessage() for r in caplog.records if "unknown rule" in r.getMessage()] != []
