import asyncio
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.mcp_password import (
    McpPasswordSettings,
    main,
    scram_sha256_verifier,
    set_mcp_server_password,
)
from tests.db_helpers import alembic_config

FIXED_SALT = bytes(range(16))
UNREACHABLE_ADMIN_URL = "postgresql+asyncpg://nobody:unused@127.0.0.1:1/none"


async def _session_user(database_url: str) -> str:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            user: str = (await conn.execute(text("SELECT session_user"))).scalar_one()
            return user
    finally:
        await engine.dispose()


async def _stored_verifier(admin_engine: AsyncEngine) -> str:
    async with admin_engine.connect() as conn:
        verifier: str = (
            await conn.execute(
                text("SELECT rolpassword FROM pg_authid WHERE rolname = 'mcp_server'")
            )
        ).scalar_one()
        return verifier


async def test_mcp_server_login_with_env_password_succeeds(
    mcp_login_engine: AsyncEngine,
) -> None:
    async with mcp_login_engine.connect() as conn:
        user: str = (await conn.execute(text("SELECT session_user"))).scalar_one()

    assert user == "mcp_server"


async def test_mcp_server_login_with_wrong_password_fails(mcp_database_url: str) -> None:
    password = make_url(mcp_database_url).password
    assert password is not None
    wrong_password = password + "x"
    wrong_url = make_url(mcp_database_url).set(password=wrong_password)

    with pytest.raises((DBAPIError, OSError)) as caught:
        await _session_user(wrong_url.render_as_string(hide_password=False))

    assert "password authentication failed" in str(caught.value)
    assert password not in str(caught.value)
    assert wrong_password not in str(caught.value)


async def test_set_mcp_server_password_stores_exact_verifier(
    tool_database_url: str, mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    password = McpPasswordSettings().mcp_password()

    await set_mcp_server_password(tool_database_url, password, FIXED_SALT)

    assert await _stored_verifier(tool_engine) == scram_sha256_verifier(
        password.get_secret_value(), FIXED_SALT
    )
    assert await _session_user(mcp_database_url) == "mcp_server"


def test_migration_upgrade_keeps_existing_mcp_password(
    mcp_database_url: str, empty_database_url: str
) -> None:
    command.upgrade(alembic_config(empty_database_url), "head")

    assert asyncio.run(_session_user(mcp_database_url)) == "mcp_server"


def test_mcp_password_main_output_never_contains_password(
    tool_database_url: str,
    mcp_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = make_url(mcp_database_url).password
    assert password is not None
    monkeypatch.setenv("DATABASE_URL", tool_database_url)
    monkeypatch.setenv("MCP_DATABASE_URL", mcp_database_url)

    main()

    out, err = capsys.readouterr()
    assert password not in out + err
    assert out == "mcp_server password set.\n"


def test_mcp_password_main_takes_admin_url_only_from_env_file(
    tool_database_url: str,
    mcp_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("MCP_DATABASE_URL", raising=False)
    (tmp_path / ".env").write_text(f"DATABASE_URL={tool_database_url}\n")
    (tmp_path / ".env.mcp").write_text(
        f"DATABASE_URL={UNREACHABLE_ADMIN_URL}\nMCP_DATABASE_URL={mcp_database_url}\n"
    )

    main()

    assert capsys.readouterr().out == "mcp_server password set.\n"
