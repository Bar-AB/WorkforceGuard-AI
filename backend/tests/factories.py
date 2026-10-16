import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.security.tenant import TenantContext
from app.services.corrections import CorrectionProposal, propose_correction

TUESDAY_8AM: Final = datetime(2026, 1, 6, 8, tzinfo=UTC)
DEFAULT_OCCURRED_ON: Final = date(2026, 1, 6)
FIX_SHIFT_START: Final = datetime(2026, 1, 5, 8, tzinfo=UTC)
FIX_SHIFT_HOURS: Final = 8
FIXED_SHIFT_END: Final = datetime(2026, 1, 5, 15, tzinfo=UTC)
SUGGEST_SHIFT_HOURS: Final = 13
SUGGEST_SHIFT_END: Final = TUESDAY_8AM + timedelta(hours=SUGGEST_SHIFT_HOURS)


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


async def set_finding_facts(
    conn: AsyncConnection,
    finding_id: uuid.UUID,
    *,
    rule_id: str = "overtime",
    severity: str = "high",
    occurred_on: date = DEFAULT_OCCURRED_ON,
) -> None:
    await conn.execute(
        text("UPDATE findings SET rule_id = :r, severity = :s, occurred_on = :o WHERE id = :id"),
        {"r": rule_id, "s": severity, "o": occurred_on, "id": finding_id},
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


async def insert_user(conn: AsyncConnection, company_id: uuid.UUID, role: str) -> uuid.UUID:
    return await _insert_returning_id(
        conn,
        "INSERT INTO users (company_id, email, display_name, role) VALUES (:c, :m, :n, :r)",
        {"c": company_id, "m": f"{uuid.uuid4().hex}@example.test", "n": "Test User", "r": role},
    )


async def insert_pending_correction(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    proposal: CorrectionProposal,
    correction_id: uuid.UUID | None = None,
) -> uuid.UUID:
    item = await propose_correction(
        conn, TenantContext(company_id), proposal, "agent:test", correction_id=correction_id
    )
    return item.id


@dataclass(frozen=True)
class ShiftFixCase:
    company_id: uuid.UUID
    employee_id: uuid.UUID
    finding_id: uuid.UUID
    shift_id: uuid.UUID
    correction_id: uuid.UUID
    admin_id: uuid.UUID

    @property
    def tenant(self) -> TenantContext:
        return TenantContext(self.company_id)


def shift_end_fix(finding_id: uuid.UUID, shift_id: uuid.UUID, ends_at: str) -> CorrectionProposal:
    return CorrectionProposal(
        finding_id=finding_id,
        target_table="shifts",
        target_id=shift_id,
        patch={"ends_at": ends_at},
        reason="The clock-out was one hour late.",
    )


async def insert_shift_fix_case(
    conn: AsyncConnection, ends_at: datetime = FIXED_SHIFT_END
) -> ShiftFixCase:
    company_id = await insert_company(conn)
    employee_id = await insert_employee(conn, company_id)
    finding_id = await insert_finding(conn, company_id, employee_id)
    shift_id = await insert_shift(conn, company_id, employee_id, FIX_SHIFT_START, FIX_SHIFT_HOURS)
    proposal = shift_end_fix(finding_id, shift_id, ends_at.isoformat())
    correction_id = await insert_pending_correction(conn, company_id, proposal)
    admin_id = await insert_user(conn, company_id, "admin")
    return ShiftFixCase(company_id, employee_id, finding_id, shift_id, correction_id, admin_id)


@dataclass(frozen=True)
class SuggestCase:
    company_id: uuid.UUID
    employee_id: uuid.UUID
    finding_id: uuid.UUID
    shift_id: uuid.UUID
    clock_in_id: uuid.UUID
    clock_out_id: uuid.UUID
    admin_id: uuid.UUID

    @property
    def tenant(self) -> TenantContext:
        return TenantContext(self.company_id)


async def insert_rival_shift_fix(
    conn: AsyncConnection, case: ShiftFixCase | SuggestCase, ends_at: str
) -> uuid.UUID:
    finding_id = await insert_finding(conn, case.company_id, case.employee_id)
    proposal = shift_end_fix(finding_id, case.shift_id, ends_at)
    return await insert_pending_correction(conn, case.company_id, proposal)


async def fix_clock_out_already(conn: AsyncConnection, case: SuggestCase) -> None:
    await conn.execute(
        text("UPDATE attendance_events SET occurred_at = :o WHERE id = :id"),
        {"o": SUGGEST_SHIFT_END - timedelta(hours=1), "id": case.clock_out_id},
    )


async def remove_overtime_policy(conn: AsyncConnection, case: SuggestCase) -> None:
    await conn.execute(
        text("DELETE FROM overtime_policies WHERE company_id = :c"), {"c": case.company_id}
    )


async def insert_suggest_case(conn: AsyncConnection) -> SuggestCase:
    company_id = await insert_company(conn)
    employee_id = await insert_employee(conn, company_id)
    await insert_overtime_policy(conn, company_id)
    dedup_key = f"overtime_breach:{employee_id}:{DEFAULT_OCCURRED_ON.isoformat()}:daily"
    finding_id = await insert_finding(conn, company_id, employee_id, dedup_key)
    await set_finding_facts(conn, finding_id, rule_id="overtime_breach")
    shift_id = await insert_shift(conn, company_id, employee_id, TUESDAY_8AM, SUGGEST_SHIFT_HOURS)
    clock_in_id = await insert_attendance_event(conn, company_id, employee_id, TUESDAY_8AM)
    clock_out_id = await insert_attendance_event(
        conn, company_id, employee_id, SUGGEST_SHIFT_END, "clock_out"
    )
    admin_id = await insert_user(conn, company_id, "admin")
    return SuggestCase(
        company_id, employee_id, finding_id, shift_id, clock_in_id, clock_out_id, admin_id
    )
