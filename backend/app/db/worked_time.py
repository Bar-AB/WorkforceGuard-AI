import uuid
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import OvertimePolicy
from app.rules.overtime import OvertimeLimits, WorkedDay

SHIFT_EARLY_CLOCK_IN = timedelta(hours=2)

_WORKED_DAYS_SQL = text("""
WITH punches AS (
    SELECT employee_id, event_type, occurred_at,
           LEAD(event_type) OVER w AS next_type,
           LEAD(occurred_at) OVER w AS next_at,
           LEAD(id) OVER w AS next_id
    FROM attendance_events
    WHERE company_id = :company_id
    WINDOW w AS (PARTITION BY employee_id ORDER BY occurred_at, id)
),
worked AS (
    SELECT p.employee_id, p.next_at, p.next_id,
           EXTRACT(EPOCH FROM p.next_at - p.occurred_at) / 3600 AS hours,
           (COALESCE(s.starts_at, p.occurred_at) AT TIME ZONE 'UTC')::date AS work_date
    FROM punches p
    LEFT JOIN LATERAL (
        SELECT starts_at FROM shifts
        WHERE company_id = :company_id
          AND employee_id = p.employee_id
          AND p.occurred_at >= starts_at - CAST(:early AS interval)
          AND p.occurred_at < ends_at
        ORDER BY abs(EXTRACT(EPOCH FROM p.occurred_at - starts_at))
        LIMIT 1
    ) s ON true
    WHERE p.event_type = 'clock_in' AND p.next_type = 'clock_out'
),
days AS (
    SELECT employee_id, work_date, SUM(hours) AS hours,
           (array_agg(next_id ORDER BY next_at DESC))[1] AS last_clock_out_id
    FROM worked
    GROUP BY employee_id, work_date
)
SELECT employee_id, work_date, last_clock_out_id,
       round(hours, 2) AS hours,
       round(SUM(hours) OVER (
           PARTITION BY employee_id, work_date - EXTRACT(DOW FROM work_date)::int
           ORDER BY work_date
       ), 2) AS week_hours_to_date
FROM days
ORDER BY employee_id, work_date
""")


async def load_worked_days(conn: AsyncConnection, company_id: uuid.UUID) -> list[WorkedDay]:
    result = await conn.execute(
        _WORKED_DAYS_SQL, {"company_id": company_id, "early": SHIFT_EARLY_CLOCK_IN}
    )
    return [
        WorkedDay(
            employee_id=row.employee_id,
            work_date=row.work_date,
            hours=row.hours,
            week_hours_to_date=row.week_hours_to_date,
            last_clock_out_id=row.last_clock_out_id,
        )
        for row in result
    ]


async def load_overtime_limits(
    conn: AsyncConnection, company_id: uuid.UUID
) -> list[OvertimeLimits]:
    result = await conn.execute(
        select(
            OvertimePolicy.max_daily_hours,
            OvertimePolicy.max_weekly_hours,
            OvertimePolicy.effective_from,
            OvertimePolicy.effective_to,
        )
        .where(OvertimePolicy.company_id == company_id)
        .order_by(OvertimePolicy.effective_from)
    )
    return [OvertimeLimits(*row) for row in result]
