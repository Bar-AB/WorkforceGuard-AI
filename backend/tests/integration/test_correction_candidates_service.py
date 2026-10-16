import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.errors import ConflictError, NotFoundError
from app.llm.propose import CandidateRow
from app.services.correction_candidates import MAX_CANDIDATE_ROWS, load_correction_candidates
from tests.factories import (
    TUESDAY_8AM,
    insert_attendance_event,
    insert_company,
    insert_employee,
    insert_shift,
    insert_suggest_case,
    set_finding_facts,
)


async def test_load_correction_candidates_returns_employee_day_rows_with_aliases(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    candidates = await load_correction_candidates(rollback_conn, case.tenant, case.finding_id)

    assert candidates.rows == (
        CandidateRow(
            alias="row_1",
            target_table="shifts",
            fields={
                "starts_at": "2026-01-06T08:00:00+00:00",
                "ends_at": "2026-01-06T21:00:00+00:00",
            },
        ),
        CandidateRow(
            alias="row_2",
            target_table="attendance_events",
            fields={
                "event_type": "clock_in",
                "occurred_at": "2026-01-06T08:00:00+00:00",
                "source": "terminal",
            },
        ),
        CandidateRow(
            alias="row_3",
            target_table="attendance_events",
            fields={
                "event_type": "clock_out",
                "occurred_at": "2026-01-06T21:00:00+00:00",
                "source": "terminal",
            },
        ),
    )
    assert candidates.targets == {
        "row_1": case.shift_id,
        "row_2": case.clock_in_id,
        "row_3": case.clock_out_id,
    }
    assert (candidates.facts.rule_id, candidates.facts.occurred_on) == (
        "overtime_breach",
        date(2026, 1, 6),
    )


async def test_load_correction_candidates_skips_other_employee_and_company_rows(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    colleague_id = await insert_employee(rollback_conn, case.company_id)
    other_company_id = await insert_company(rollback_conn)
    stranger_id = await insert_employee(rollback_conn, other_company_id)
    await insert_shift(rollback_conn, case.company_id, colleague_id, TUESDAY_8AM, 9)
    await insert_attendance_event(rollback_conn, other_company_id, stranger_id, TUESDAY_8AM)
    await insert_attendance_event(
        rollback_conn, case.company_id, case.employee_id, TUESDAY_8AM + timedelta(days=3)
    )

    candidates = await load_correction_candidates(rollback_conn, case.tenant, case.finding_id)

    assert set(candidates.targets.values()) == {case.shift_id, case.clock_in_id, case.clock_out_id}


async def test_load_correction_candidates_caps_rows(rollback_conn: AsyncConnection) -> None:
    case = await insert_suggest_case(rollback_conn)
    for minute in range(MAX_CANDIDATE_ROWS):
        await insert_attendance_event(
            rollback_conn,
            case.company_id,
            case.employee_id,
            TUESDAY_8AM + timedelta(minutes=minute),
        )

    candidates = await load_correction_candidates(rollback_conn, case.tenant, case.finding_id)

    assert len(candidates.rows) == MAX_CANDIDATE_ROWS
    assert candidates.rows[0].target_table == "shifts"
    assert candidates.rows[-1].alias == f"row_{MAX_CANDIDATE_ROWS}"


async def test_load_correction_candidates_fields_exclude_device_and_note(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await rollback_conn.execute(
        text(
            "UPDATE attendance_events SET device_id = 'gate-7', note = 'Left early' WHERE id = :id"
        ),
        {"id": case.clock_in_id},
    )

    candidates = await load_correction_candidates(rollback_conn, case.tenant, case.finding_id)

    assert [sorted(row.fields) for row in candidates.rows] == [
        ["ends_at", "starts_at"],
        ["event_type", "occurred_at", "source"],
        ["event_type", "occurred_at", "source"],
    ]


async def test_load_correction_candidates_dismissed_finding_is_conflict(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await rollback_conn.execute(
        text("UPDATE findings SET status = 'dismissed' WHERE id = :id"), {"id": case.finding_id}
    )

    with pytest.raises(ConflictError, match="dismissed"):
        await load_correction_candidates(rollback_conn, case.tenant, case.finding_id)


async def test_load_correction_candidates_unknown_finding_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    with pytest.raises(NotFoundError):
        await load_correction_candidates(rollback_conn, case.tenant, uuid.uuid4())


@pytest.mark.parametrize("day", [date(1970, 1, 1), date(2100, 12, 31)])
async def test_load_correction_candidates_edge_day_keeps_window_in_range(
    rollback_conn: AsyncConnection, day: date
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await set_finding_facts(rollback_conn, case.finding_id, occurred_on=day)

    candidates = await load_correction_candidates(rollback_conn, case.tenant, case.finding_id)

    assert (candidates.rows, candidates.facts.occurred_on) == ((), day)
