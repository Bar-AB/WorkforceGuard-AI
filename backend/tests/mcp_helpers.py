import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import Client
from mcp.types import CallToolResult, TextContent
from pydantic import SecretStr

from mcp_server.server import create_server
from mcp_server.settings import McpSettings


@asynccontextmanager
async def open_mcp_client(database_url: str, company_id: uuid.UUID) -> AsyncIterator[Client]:
    settings = McpSettings(mcp_database_url=SecretStr(database_url), mcp_company_id=company_id)
    async with Client(create_server(settings), raise_exceptions=True) as client:
        yield client


def tool_error_text(result: CallToolResult) -> str:
    assert result.is_error
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text
