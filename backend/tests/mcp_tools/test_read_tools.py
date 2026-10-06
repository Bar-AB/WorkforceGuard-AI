import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.access_logs import AccessLogsPage
from app.services.attendance import AttendanceEventsPage
from app.services.payroll import PayrollSummary
from app.services.shifts import ShiftsPage
from tests.factories import (
    insert_access_log,
    insert_attendance_event,
    insert_company,
    insert_employee,
    insert_payroll_run,
    insert_shift,
)
from tests.mcp_helpers import open_mcp_client, tool_error_text

START = datetime(2026, 1, 5, tzinfo=UTC)
WINDOW = {"start": START.isoformat(), "end": (START + timedelta(days=7)).isoformat()}


async def test_get_attendance_events_tool_returns_structured_page(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)
        employee_id = await insert_employee(setup, company_id)
        event_id = await insert_attendance_event(setup, company_id, employee_id, START)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool("get_attendance_events", WINDOW)

    page = AttendanceEventsPage.model_validate(result.structured_content)
    assert [item.id for item in page.items] == [event_id]


async def test_get_shifts_tool_returns_structured_page(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)
        employee_id = await insert_employee(setup, company_id)
        shift_id = await insert_shift(setup, company_id, employee_id, START, 8)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool("get_shifts", {**WINDOW, "employee_id": str(employee_id)})

    page = ShiftsPage.model_validate(result.structured_content)
    assert [item.id for item in page.items] == [shift_id]


async def test_get_access_logs_tool_returns_structured_page(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)
        employee_id = await insert_employee(setup, company_id)
        log_id = await insert_access_log(setup, company_id, employee_id, START)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool("get_access_logs", {**WINDOW, "limit": 1})

    page = AccessLogsPage.model_validate(result.structured_content)
    assert [item.id for item in page.items] == [log_id]


async def test_get_payroll_summary_tool_returns_structured_summary(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)
        employee_id = await insert_employee(setup, company_id)
        await insert_payroll_run(
            setup,
            company_id,
            employee_id,
            date(2026, 1, 1),
            date(2026, 1, 14),
            regular_hours=Decimal("80"),
            overtime_hours=Decimal("4"),
            gross_pay=Decimal("1200"),
        )

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "get_payroll_summary", {"period_from": "2026-01-01", "period_to": "2026-01-31"}
        )

    summary = PayrollSummary.model_validate(result.structured_content)
    assert [(period.headcount, period.gross_pay) for period in summary.periods] == [
        (1, Decimal("1200"))
    ]


async def test_get_attendance_events_tool_bad_window_is_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "get_attendance_events", {"start": "2026-01-05T00:00:00", "end": WINDOW["end"]}
        )

    assert "time zone" in tool_error_text(result)


async def test_payroll_tool_output_schema_has_no_employee_money(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        listing = await client.list_tools()

    payroll_tool = next(tool for tool in listing.tools if tool.name == "get_payroll_summary")
    assert payroll_tool.output_schema is not None
    employee_schema = payroll_tool.output_schema["$defs"]["PayrollEmployeeHours"]
    assert set(employee_schema["properties"]) == {
        "payroll_run_id",
        "employee_id",
        "regular_hours",
        "overtime_hours",
    }


async def test_get_shifts_tool_unknown_employee_is_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)
    other_company_employee_id = await _commit_employee_of_other_company(tool_engine)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "get_shifts", {**WINDOW, "employee_id": str(other_company_employee_id)}
        )

    assert "not found" in tool_error_text(result)


async def test_get_access_logs_tool_extreme_window_is_domain_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "get_access_logs",
            {"start": "0001-01-01T00:00:00+05:00", "end": "0001-01-02T00:00:00+05:00"},
        )

    assert "between 1970 and 2100" in tool_error_text(result)


async def _commit_employee_of_other_company(engine: AsyncEngine) -> uuid.UUID:
    async with engine.begin() as setup:
        return await insert_employee(setup, await insert_company(setup))
