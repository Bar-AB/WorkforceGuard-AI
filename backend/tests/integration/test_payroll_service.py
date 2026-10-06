from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncConnection

from app.security.tenant import TenantContext
from app.services.payroll import PayrollEmployeeHours, get_payroll_summary
from tests.factories import insert_company, insert_employee, insert_payroll_run

FIRST_START, FIRST_END = date(2026, 1, 1), date(2026, 1, 14)
SECOND_START, SECOND_END = date(2026, 1, 15), date(2026, 1, 28)


async def test_get_payroll_summary_groups_totals_per_period(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    alice = await insert_employee(rollback_conn, company_id)
    bob = await insert_employee(rollback_conn, company_id)
    alice_first = await insert_payroll_run(
        rollback_conn,
        company_id,
        alice,
        FIRST_START,
        FIRST_END,
        regular_hours=Decimal("80"),
        overtime_hours=Decimal("5.5"),
        gross_pay=Decimal("1000"),
    )
    bob_first = await insert_payroll_run(
        rollback_conn,
        company_id,
        bob,
        FIRST_START,
        FIRST_END,
        regular_hours=Decimal("70"),
        overtime_hours=Decimal("0"),
        gross_pay=Decimal("800.25"),
    )
    alice_second = await insert_payroll_run(
        rollback_conn,
        company_id,
        alice,
        SECOND_START,
        SECOND_END,
        regular_hours=Decimal("75"),
        overtime_hours=Decimal("2"),
        gross_pay=Decimal("900"),
    )

    summary = await get_payroll_summary(
        rollback_conn, TenantContext(company_id), FIRST_START, SECOND_START
    )

    first, second = summary.periods
    assert (first.period_start, first.period_end, first.headcount) == (FIRST_START, FIRST_END, 2)
    assert (first.regular_hours, first.overtime_hours) == (Decimal("150"), Decimal("5.5"))
    assert first.gross_pay == Decimal("1800.25")
    assert {
        (row.payroll_run_id, row.employee_id, row.regular_hours) for row in first.employees
    } == {
        (alice_first, alice, Decimal("80")),
        (bob_first, bob, Decimal("70")),
    }
    assert (second.period_start, second.headcount, second.gross_pay) == (
        SECOND_START,
        1,
        Decimal("900"),
    )
    assert [(row.payroll_run_id, row.overtime_hours) for row in second.employees] == [
        (alice_second, Decimal("2"))
    ]


async def test_get_payroll_summary_other_company_rows_are_excluded(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    other_company_id = await insert_company(rollback_conn)
    await insert_payroll_run(
        rollback_conn,
        other_company_id,
        await insert_employee(rollback_conn, other_company_id),
        FIRST_START,
        FIRST_END,
        regular_hours=Decimal("80"),
        overtime_hours=Decimal("0"),
        gross_pay=Decimal("1000"),
    )

    summary = await get_payroll_summary(
        rollback_conn, TenantContext(company_id), FIRST_START, FIRST_END
    )

    assert summary.periods == []


async def test_get_payroll_summary_empty_window_returns_no_periods(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    await insert_payroll_run(
        rollback_conn,
        company_id,
        await insert_employee(rollback_conn, company_id),
        FIRST_START,
        FIRST_END,
        regular_hours=Decimal("80"),
        overtime_hours=Decimal("0"),
        gross_pay=Decimal("1000"),
    )

    summary = await get_payroll_summary(
        rollback_conn, TenantContext(company_id), date(2026, 1, 2), date(2026, 1, 14)
    )

    assert summary.periods == []


def test_payroll_summary_employee_rows_have_no_money_field() -> None:
    assert set(PayrollEmployeeHours.model_fields) == {
        "payroll_run_id",
        "employee_id",
        "regular_hours",
        "overtime_hours",
    }
