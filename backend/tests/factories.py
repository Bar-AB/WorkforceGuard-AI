import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

TUESDAY_8AM: Final = datetime(2026, 1, 6, 8, tzinfo=UTC)


async def insert_company(conn: AsyncConnection) -> uuid.UUID:
    company_id = uuid.uuid4()
    await conn.execute(
        text("INSERT INTO companies (id, name) VALUES (:id, :name)"),
        {"id": company_id, "name": f"Company {company_id}"},
    )
    return company_id


async def insert_employee(conn: AsyncConnection, company_id: uuid.UUID) -> uuid.UUID:
    employee_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO employees (id, company_id, employee_number, full_name, hired_on) "
            "VALUES (:id, :company_id, :number, 'Test Person', '2026-01-01')"
        ),
        {"id": employee_id, "company_id": company_id, "number": employee_id.hex[:8]},
    )
    return employee_id


async def insert_finding(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    dedup_key: str | None = None,
    *,
    detected_at: datetime | None = None,
) -> uuid.UUID:
    result = await conn.execute(
        text(
            "INSERT INTO findings (company_id, employee_id, rule_id, rule_version, severity, "
            "summary, occurred_on, dedup_key, detected_at) VALUES (:c, :e, 'overtime', 'v1', "
            "'high', 'Too many hours', '2026-01-06', :k, COALESCE(CAST(:d AS timestamptz), now())) "
            "RETURNING id"
        ),
        {"c": company_id, "e": employee_id, "k": dedup_key or uuid.uuid4().hex, "d": detected_at},
    )
    finding_id: uuid.UUID = result.scalar_one()
    return finding_id


async def mark_finding_explained(
    conn: AsyncConnection, finding_id: uuid.UUID, explanation: str = "Already explained."
) -> None:
    await conn.execute(
        text(
            "UPDATE findings SET explanation = :t, explanation_source = 'llm', "
            "explanation_prompt_version = 'explain_finding.v1' WHERE id = :id"
        ),
        {"t": explanation, "id": finding_id},
    )


async def insert_overtime_policy(conn: AsyncConnection, company_id: uuid.UUID) -> None:
    await conn.execute(
        text(
            "INSERT INTO overtime_policies (company_id, name, max_daily_hours, max_weekly_hours, "
            "effective_from) VALUES (:c, 'Israel', 12, 58, '2025-01-01')"
        ),
        {"c": company_id},
    )


async def insert_worked_shift(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    starts_at: datetime,
    hours: int,
) -> None:
    """A shift worked exactly as planned: clock in at the start, clock out at the end."""
    ends_at = starts_at + timedelta(hours=hours)
    tenant = {"c": company_id, "e": employee_id}
    await conn.execute(
        text(
            "INSERT INTO shifts (company_id, employee_id, starts_at, ends_at) "
            "VALUES (:c, :e, :s, :x)"
        ),
        {**tenant, "s": starts_at, "x": ends_at},
    )
    await conn.execute(
        text(
            "INSERT INTO attendance_events (company_id, employee_id, event_type, occurred_at) "
            "VALUES (:c, :e, 'clock_in', :s), (:c, :e, 'clock_out', :x)"
        ),
        {**tenant, "s": starts_at, "x": ends_at},
    )


async def insert_company_with_long_day(conn: AsyncConnection) -> tuple[uuid.UUID, uuid.UUID]:
    company_id = await insert_company(conn)
    employee_id = await insert_employee(conn, company_id)
    await insert_overtime_policy(conn, company_id)
    await insert_worked_shift(conn, company_id, employee_id, TUESDAY_8AM, hours=13)
    return company_id, employee_id


async def insert_daily_long_shifts(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    first_start: datetime,
    days: int,
) -> None:
    """One 13 h shift a day for `days` days, each worked as planned. Bulk, for volume tests."""
    params = {"c": company_id, "e": employee_id, "first": first_start, "days": days, "h": 13}
    starts = (
        "WITH starts AS (SELECT CAST(:first AS timestamptz) + make_interval(days => n) AS s "
        "FROM generate_series(0, CAST(:days AS integer) - 1) AS n) "
    )
    ends = "s + make_interval(hours => CAST(:h AS integer))"
    await conn.execute(
        text(
            f"{starts}INSERT INTO shifts (company_id, employee_id, starts_at, ends_at) "
            f"SELECT :c, :e, s, {ends} FROM starts"
        ),
        params,
    )
    await conn.execute(
        text(
            f"{starts}INSERT INTO attendance_events (company_id, employee_id, event_type, "
            f"occurred_at) SELECT CAST(:c AS uuid), CAST(:e AS uuid), 'clock_in', s FROM starts "
            f"UNION ALL SELECT CAST(:c AS uuid), CAST(:e AS uuid), 'clock_out', {ends} FROM starts"
        ),
        params,
    )


async def _insert_returning_id(
    conn: AsyncConnection, statement: str, params: dict[str, object]
) -> uuid.UUID:
    result = await conn.execute(text(f"{statement} RETURNING id"), params)
    row_id: uuid.UUID = result.scalar_one()
    return row_id


async def insert_shift(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    starts_at: datetime,
    hours: int,
) -> uuid.UUID:
    return await _insert_returning_id(
        conn,
        "INSERT INTO shifts (company_id, employee_id, starts_at, ends_at) VALUES (:c, :e, :s, :x)",
        {
            "c": company_id,
            "e": employee_id,
            "s": starts_at,
            "x": starts_at + timedelta(hours=hours),
        },
    )


async def insert_attendance_event(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    occurred_at: datetime,
    event_type: str = "clock_in",
) -> uuid.UUID:
    return await _insert_returning_id(
        conn,
        "INSERT INTO attendance_events (company_id, employee_id, event_type, occurred_at) "
        "VALUES (:c, :e, :t, :o)",
        {"c": company_id, "e": employee_id, "t": event_type, "o": occurred_at},
    )


async def insert_access_log(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    occurred_at: datetime,
) -> uuid.UUID:
    return await _insert_returning_id(
        conn,
        "INSERT INTO access_logs (company_id, employee_id, door, direction, granted, occurred_at) "
        "VALUES (:c, :e, 'main', 'in', true, :o)",
        {"c": company_id, "e": employee_id, "o": occurred_at},
    )


async def insert_payroll_run(  # noqa: PLR0913 - one argument per payroll column under test
    conn: AsyncConnection,
    company_id: uuid.UUID,
    employee_id: uuid.UUID,
    period_start: date,
    period_end: date,
    *,
    regular_hours: Decimal,
    overtime_hours: Decimal,
    gross_pay: Decimal,
) -> uuid.UUID:
    return await _insert_returning_id(
        conn,
        "INSERT INTO payroll_runs (company_id, employee_id, period_start, period_end, "
        "regular_hours, overtime_hours, gross_pay) VALUES (:c, :e, :ps, :pe, :r, :o, :g)",
        {
            "c": company_id,
            "e": employee_id,
            "ps": period_start,
            "pe": period_end,
            "r": regular_hours,
            "o": overtime_hours,
            "g": gross_pay,
        },
    )
