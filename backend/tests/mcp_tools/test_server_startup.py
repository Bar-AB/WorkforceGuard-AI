import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.security.mcp_login_guard import StartupError
from tests.factories import insert_company
from tests.mcp_helpers import open_mcp_client


async def test_server_start_with_unknown_company_fails_loudly(mcp_database_url: str) -> None:
    unknown_company_id = uuid.uuid4()

    with pytest.raises(StartupError, match="MCP_COMPANY_ID") as caught:
        async with open_mcp_client(mcp_database_url, unknown_company_id):
            pass

    assert str(unknown_company_id) in str(caught.value)


async def test_server_start_with_admin_login_is_refused(
    tool_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    with pytest.raises(StartupError, match="MCP_DATABASE_URL"):
        async with open_mcp_client(tool_database_url, company_id):
            pass


async def test_server_start_with_known_company_succeeds(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        tools = (await client.list_tools()).tools

    assert tools
