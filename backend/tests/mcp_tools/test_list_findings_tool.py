from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.findings import FindingsPage
from tests.factories import insert_company, insert_employee, insert_finding
from tests.mcp_helpers import open_mcp_client, tool_error_text


async def test_list_findings_tool_returns_only_tenant_findings(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)
        other_company_id = await insert_company(setup)
        own_finding = await insert_finding(
            setup, company_id, await insert_employee(setup, company_id)
        )
        await insert_finding(
            setup, other_company_id, await insert_employee(setup, other_company_id)
        )

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool("list_findings", {})

    page = FindingsPage.model_validate(result.structured_content)
    assert [item.id for item in page.items] == [own_finding]


async def test_list_findings_tool_bad_cursor_is_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool("list_findings", {"cursor": "not-a-cursor"})

    assert "Invalid cursor" in tool_error_text(result)


async def test_list_findings_tool_limit_out_of_range_is_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool("list_findings", {"limit": 101})

    assert "limit" in tool_error_text(result)
