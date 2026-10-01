import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AttendanceEvent, OvertimePolicy, Shift
from app.db.worked_time import load_overtime_limits, load_worked_days
from app.rules.overtime import OvertimeLimits
from tests.factories import insert_company, insert_employee

# 2026-01-08 is a Thursday; the Israeli week runs Sunday to Saturday.
THURSDAY = date(2026, 1, 8)
SATURDAY = date(2026, 1, 10)
SUNDAY = date(2026, 1, 11)


def _utc(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(
        hours=hour, minutes=minute
    )


async def _work(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    shift: tuple[datetime, datetime],
    punches: tuple[datetime, datetime | None],
) -> uuid.UUID:
    """Adds a shift and its punches. Returns the clock-out id (or clock-in if none)."""
    tenant = {"company_id": company_id, "employee_id": employee_id}
    await conn.execute(insert(Shift), [{**tenant, "starts_at": shift[0], "ends_at": shift[1]}])
    clock_in, clock_out = punches
    event_id = uuid.uuid4()
    await conn.execute(
        insert(AttendanceEvent),
        [{**tenant, "id": event_id, "event_type": "clock_in", "occurred_at": clock_in}],
    )
    if clock_out is None:
        return event_id
    out_id = uuid.uuid4()
    await conn.execute(
        insert(AttendanceEvent),
        [{**tenant, "id": out_id, "event_type": "clock_out", "occurred_at": clock_out}],
    )
    return out_id


async def test_load_worked_days_night_shift_crossing_midnight_counts_on_shift_start_date(
    rollback_conn: AsyncConnection,
) -> None:
    company = await insert_company(rollback_conn)
    employee = await insert_employee(rollback_conn, company)
    shift = (_utc(THURSDAY, 23), _utc(THURSDAY, 36))
    clock_out = await _work(
        rollback_conn, company, employee, shift, (_utc(THURSDAY, 22, 45), _utc(THURSDAY, 35, 30))
    )

    days = await load_worked_days(rollback_conn, company)

    assert [(d.work_date, d.hours, d.last_clock_out_id) for d in days] == [
        (THURSDAY, Decimal("12.75"), clock_out)
    ]


async def test_load_worked_days_clock_in_after_midnight_belongs_to_night_shift(
    rollback_conn: AsyncConnection,
) -> None:
    company = await insert_company(rollback_conn)
    employee = await insert_employee(rollback_conn, company)
    shift = (_utc(THURSDAY, 23), _utc(THURSDAY, 31))
    await _work(
        rollback_conn, company, employee, shift, (_utc(THURSDAY, 24, 10), _utc(THURSDAY, 31))
    )

    days = await load_worked_days(rollback_conn, company)

    assert [d.work_date for d in days] == [THURSDAY]


async def test_load_worked_days_week_total_resets_on_sunday(
    rollback_conn: AsyncConnection,
) -> None:
    company = await insert_company(rollback_conn)
    employee = await insert_employee(rollback_conn, company)
    for day, hour in ((THURSDAY, 7), (SATURDAY, 23), (SUNDAY, 12)):
        shift = (_utc(day, hour), _utc(day, hour + 10))
        await _work(rollback_conn, company, employee, shift, shift)

    days = await load_worked_days(rollback_conn, company)

    assert [(d.work_date, d.week_hours_to_date) for d in days] == [
        (THURSDAY, Decimal("10.00")),
        (SATURDAY, Decimal("20.00")),
        (SUNDAY, Decimal("10.00")),
    ]


async def test_load_worked_days_clock_in_without_clock_out_is_skipped(
    rollback_conn: AsyncConnection,
) -> None:
    company = await insert_company(rollback_conn)
    employee = await insert_employee(rollback_conn, company)
    shift = (_utc(THURSDAY, 7), _utc(THURSDAY, 15))
    await _work(rollback_conn, company, employee, shift, (_utc(THURSDAY, 7), None))

    assert await load_worked_days(rollback_conn, company) == []


async def test_load_worked_days_only_returns_the_given_company(
    rollback_conn: AsyncConnection,
) -> None:
    mine, other = await insert_company(rollback_conn), await insert_company(rollback_conn)
    for company in (mine, other):
        employee = await insert_employee(rollback_conn, company)
        shift = (_utc(THURSDAY, 7), _utc(THURSDAY, 15))
        await _work(rollback_conn, company, employee, shift, shift)

    days = await load_worked_days(rollback_conn, mine)

    assert len(days) == 1


async def test_load_overtime_limits_returns_only_the_company_policies(
    rollback_conn: AsyncConnection,
) -> None:
    mine, other = await insert_company(rollback_conn), await insert_company(rollback_conn)
    for company, daily in ((mine, 12), (other, 9)):
        await rollback_conn.execute(
            insert(OvertimePolicy),
            [
                {
                    "company_id": company,
                    "name": "Policy",
                    "max_daily_hours": Decimal(daily),
                    "max_weekly_hours": Decimal(58),
                    "effective_from": date(2025, 1, 1),
                }
            ],
        )

    limits = await load_overtime_limits(rollback_conn, mine)

    assert limits == [OvertimeLimits(Decimal(12), Decimal(58), date(2025, 1, 1), None)]
