import asyncio
import sys

import pytest
from langgraph.checkpoint.postgres.base import BasePostgresSaver
from psycopg import AsyncConnection, OperationalError
from psycopg.conninfo import conninfo_to_dict

from app.db.checkpoint_tables import CHECKPOINT_SCHEMA_VERSION, is_alembic_managed
from app.graph.checkpointer import (
    CheckpointerUnavailableError,
    open_checkpointer,
    psycopg_conninfo,
)

UNREACHABLE_URL = "postgresql+asyncpg://app:secret@127.0.0.1:1/workforce"
LEAKY_PASSWORD = "s3cr3t-pw"


def test_psycopg_conninfo_swaps_asyncpg_driver() -> None:
    conninfo = psycopg_conninfo("postgresql+asyncpg://app:secret@db.local:5433/workforce")

    assert conninfo_to_dict(conninfo) == {
        "host": "db.local",
        "port": "5433",
        "dbname": "workforce",
        "user": "app",
        "password": "secret",
        "connect_timeout": "10",
    }


def test_psycopg_conninfo_keeps_encoded_password() -> None:
    conninfo = psycopg_conninfo(
        "postgresql+asyncpg://app:p%40ss%2Fw%25rd%20x%27y@localhost:5432/wg"
    )

    assert conninfo_to_dict(conninfo)["password"] == "p@ss/w%rd x'y"


def test_psycopg_conninfo_maps_asyncpg_ssl_to_sslmode() -> None:
    conninfo = psycopg_conninfo("postgresql+asyncpg://app:secret@localhost/wg?ssl=require")

    assert conninfo_to_dict(conninfo)["sslmode"] == "require"


def test_psycopg_conninfo_keeps_libpq_query_params() -> None:
    conninfo = psycopg_conninfo(
        "postgresql+asyncpg://app:secret@localhost/wg?application_name=workforceguard"
    )

    assert conninfo_to_dict(conninfo)["application_name"] == "workforceguard"


def test_psycopg_conninfo_refuses_unknown_ssl_value() -> None:
    with pytest.raises(ValueError, match="ssl"):
        psycopg_conninfo("postgresql+asyncpg://app:secret@localhost/wg?ssl=bogus")


def test_psycopg_conninfo_refuses_asyncpg_only_params() -> None:
    with pytest.raises(ValueError, match="prepared_statement_cache_size"):
        psycopg_conninfo(
            "postgresql+asyncpg://app:secret@localhost/wg?prepared_statement_cache_size=0"
        )


def test_psycopg_conninfo_sets_connect_timeout_over_url_value() -> None:
    conninfo = psycopg_conninfo("postgresql+asyncpg://app:secret@localhost/wg?connect_timeout=abc")

    assert conninfo_to_dict(conninfo)["connect_timeout"] == "10"


def test_psycopg_conninfo_refuses_password_query_param() -> None:
    with pytest.raises(ValueError, match="password"):
        psycopg_conninfo("postgresql+asyncpg://app:secret@localhost/wg?password=other")


async def _raise_leaky_error(*args: object, **kwargs: object) -> AsyncConnection:
    raise OperationalError(f"connection failed for password {LEAKY_PASSWORD}")


async def test_open_checkpointer_redacts_password_from_connect_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AsyncConnection, "connect", _raise_leaky_error)

    with pytest.raises(CheckpointerUnavailableError) as raised:
        async with open_checkpointer(
            f"postgresql+asyncpg://app:{LEAKY_PASSWORD}@127.0.0.1:1/workforce"
        ):
            pass

    assert str(raised.value) == "connection failed for password ***"
    assert raised.value.__cause__ is None


async def _open_unreachable() -> None:
    async with open_checkpointer(UNREACHABLE_URL):
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="ProactorEventLoop exists only on Windows")
def test_open_checkpointer_on_proactor_loop_fails_fast() -> None:
    with pytest.raises(CheckpointerUnavailableError, match="ProactorEventLoop"):
        asyncio.run(_open_unreachable(), loop_factory=asyncio.ProactorEventLoop)


@pytest.mark.parametrize(
    "table",
    ["checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"],
)
def test_is_alembic_managed_skips_checkpoint_tables(table: str) -> None:
    assert is_alembic_managed(table, "table", {}) is False


@pytest.mark.parametrize(
    ("name", "type_"),
    [
        ("findings", "table"),
        ("proposed_corrections", "table"),
        ("checkpoints_thread_id_idx", "index"),
        ("thread_id", "column"),
        (None, "schema"),
    ],
)
def test_is_alembic_managed_keeps_app_tables(name: str | None, type_: str) -> None:
    assert is_alembic_managed(name, type_, {}) is True


def test_installed_saver_schema_version_matches() -> None:
    assert len(BasePostgresSaver.MIGRATIONS) - 1 == CHECKPOINT_SCHEMA_VERSION


async def test_test_loop_is_selector() -> None:
    assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
