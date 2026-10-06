from sqlalchemy.ext.asyncio import AsyncEngine

from tests.factories import insert_company
from tests.mcp_helpers import open_mcp_client

SIX_TOOLS = {
    "get_attendance_events",
    "get_shifts",
    "get_access_logs",
    "get_payroll_summary",
    "list_findings",
    "propose_correction",
}


async def test_tools_list_is_exactly_the_six_tools(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        listing = await client.list_tools()

    assert {tool.name for tool in listing.tools} == SIX_TOOLS
    assert [tool.name for tool in listing.tools if tool.output_schema is None] == []
