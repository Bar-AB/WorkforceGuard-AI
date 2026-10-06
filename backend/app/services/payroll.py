import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import PayrollRun
from app.errors import InvalidInputError
from app.security.tenant import TenantContext

MAX_PAYROLL_WINDOW = timedelta(days=31)


class PayrollEmployeeHours(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payroll_run_id: uuid.UUID
    employee_id: uuid.UUID
    regular_hours: Decimal
    overtime_hours: Decimal


class PayPeriodSummary(BaseModel):
    period_start: date
    period_end: date
    headcount: int
    regular_hours: Decimal
    overtime_hours: Decimal
    gross_pay: Decimal
    employees: list[PayrollEmployeeHours]


class PayrollSummary(BaseModel):
    periods: list[PayPeriodSummary]


def check_period_window(period_from: date, period_to: date) -> None:
    if period_to < period_from:
        raise InvalidInputError("period_to must not be before period_from.")
    if period_to - period_from > MAX_PAYROLL_WINDOW:
        raise InvalidInputError(
            f"The payroll window can span at most {MAX_PAYROLL_WINDOW.days} days."
        )


async def get_payroll_summary(
    conn: AsyncConnection, tenant: TenantContext, period_from: date, period_to: date
) -> PayrollSummary:
    check_period_window(period_from, period_to)
    in_window = (
        PayrollRun.company_id == tenant.company_id,
        PayrollRun.period_start.between(period_from, period_to),
    )
    hours = await _employee_hours_by_period(conn, in_window)
    totals = await conn.execute(_PERIOD_TOTALS.where(*in_window))
    periods = [
        PayPeriodSummary(
            period_start=row.period_start,
            period_end=row.period_end,
            headcount=row.headcount,
            regular_hours=row.regular_hours,
            overtime_hours=row.overtime_hours,
            gross_pay=row.gross_pay,
            employees=hours[(row.period_start, row.period_end)],
        )
        for row in totals
    ]
    return PayrollSummary(periods=periods)


_PERIOD_TOTALS = (
    select(
        PayrollRun.period_start,
        PayrollRun.period_end,
        func.count(PayrollRun.employee_id.distinct()).label("headcount"),
        func.sum(PayrollRun.regular_hours).label("regular_hours"),
        func.sum(PayrollRun.overtime_hours).label("overtime_hours"),
        func.sum(PayrollRun.gross_pay).label("gross_pay"),
    )
    .group_by(PayrollRun.period_start, PayrollRun.period_end)
    .order_by(PayrollRun.period_start, PayrollRun.period_end)
)

_EMPLOYEE_HOURS = select(
    PayrollRun.period_start,
    PayrollRun.period_end,
    PayrollRun.id.label("payroll_run_id"),
    PayrollRun.employee_id,
    PayrollRun.regular_hours,
    PayrollRun.overtime_hours,
).order_by(PayrollRun.period_start, PayrollRun.employee_id)


async def _employee_hours_by_period(
    conn: AsyncConnection, in_window: tuple[ColumnElement[bool], ...]
) -> defaultdict[tuple[date, date], list[PayrollEmployeeHours]]:
    by_period: defaultdict[tuple[date, date], list[PayrollEmployeeHours]] = defaultdict(list)
    for row in await conn.execute(_EMPLOYEE_HOURS.where(*in_window)):
        by_period[(row.period_start, row.period_end)].append(
            PayrollEmployeeHours.model_validate(row)
        )
    return by_period
