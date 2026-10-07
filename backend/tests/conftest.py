import asyncio
import secrets
from collections.abc import AsyncIterator, Iterator
from typing import Final

import pytest
from alembic import command
from langsmith import utils as langsmith_utils
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.mcp_password import (
    MCP_LOGIN_ROLE,
    SALT_BYTES,
    McpPasswordSettings,
    set_mcp_server_password,
)
from tests.db_helpers import alembic_config, create_empty_database, drop_database

LANGSMITH_TRACING_VARS: Final = (
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
)


@pytest.fixture(autouse=True)
def langsmith_tracing_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LANGSMITH_TRACING_VARS:
        monkeypatch.setenv(name, "false")
    langsmith_utils.get_env_var.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    url = create_empty_database()
    try:
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    url = create_empty_database()
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="session")
def tool_database_url() -> Iterator[str]:
    url = create_empty_database()
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="session")
def mcp_database_url(tool_database_url: str) -> str:
    password = McpPasswordSettings().mcp_password()
    asyncio.run(
        set_mcp_server_password(tool_database_url, password, secrets.token_bytes(SALT_BYTES))
    )
    url = make_url(tool_database_url).set(
        username=MCP_LOGIN_ROLE, password=password.get_secret_value()
    )
    return url.render_as_string(hide_password=False)


@pytest.fixture
async def mcp_login_engine(mcp_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(mcp_database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def tool_engine(tool_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(tool_database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def migrated_engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def rollback_conn(migrated_engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Connection whose work is always rolled back, so tests never leak rows."""
    async with migrated_engine.connect() as conn:
        transaction = await conn.begin()
        yield conn
        await transaction.rollback()
