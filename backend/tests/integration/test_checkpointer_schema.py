import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from sqlalchemy import Connection, inspect, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.checkpoint_tables import CHECKPOINT_SCHEMA_VERSION, CHECKPOINT_TABLES
from app.graph.checkpointer import (
    CHECKPOINT_POOL_TIMEOUT_SECONDS,
    CheckpointerUnavailableError,
    open_checkpointer,
)
from tests.db_helpers import create_empty_database, drop_database

WRONG_PASSWORD = "not the%password"
THREAD_CONFIG: RunnableConfig = {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}}


@pytest.fixture
def saver_setup_database_url() -> Iterator[str]:
    url = create_empty_database()
    try:
        yield url
    finally:
        drop_database(url)


async def _migration_versions(engine: AsyncEngine) -> tuple[int, int]:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT max(v), count(*) FROM checkpoint_migrations"))
        top, count = result.one()
    return top, count


def _table_shape(sync_conn: Connection, table: str) -> dict[str, Any]:
    inspector = inspect(sync_conn)
    return {
        "columns": [
            (column["name"], str(column["type"]), column["nullable"], column["default"])
            for column in inspector.get_columns(table)
        ],
        "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), index["unique"])
            for index in inspector.get_indexes(table)
        ),
    }


def _checkpoint_schema(sync_conn: Connection) -> dict[str, dict[str, Any]]:
    return {table: _table_shape(sync_conn, table) for table in sorted(CHECKPOINT_TABLES)}


async def _schema_of(database_url: str) -> dict[str, dict[str, Any]]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_checkpoint_schema)
    finally:
        await engine.dispose()


async def test_saver_setup_after_migrations_changes_nothing(
    committing_database_url: str, committing_engine: AsyncEngine
) -> None:
    before = await _migration_versions(committing_engine)

    async with open_checkpointer(committing_database_url) as saver:
        await saver.setup()

    assert before == (CHECKPOINT_SCHEMA_VERSION, CHECKPOINT_SCHEMA_VERSION + 1)
    assert await _migration_versions(committing_engine) == before


async def test_migrated_schema_matches_saver_setup_schema(
    committing_database_url: str, saver_setup_database_url: str
) -> None:
    async with open_checkpointer(saver_setup_database_url) as saver:
        await saver.setup()

    assert await _schema_of(committing_database_url) == await _schema_of(saver_setup_database_url)


async def _put_empty_checkpoint(saver: AsyncPostgresSaver, config: RunnableConfig) -> str:
    checkpoint = empty_checkpoint()
    await saver.aput(config, checkpoint, {}, {})
    return checkpoint["id"]


async def test_open_checkpointer_persists_between_pools(committing_database_url: str) -> None:
    async with open_checkpointer(committing_database_url) as saver:
        checkpoint_id = await _put_empty_checkpoint(saver, THREAD_CONFIG)

    async with open_checkpointer(committing_database_url) as saver:
        stored = await saver.aget_tuple(THREAD_CONFIG)

    assert stored is not None
    assert stored.checkpoint["id"] == checkpoint_id


async def _put_checkpoint_with_values_and_writes(saver: AsyncPostgresSaver) -> None:
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"facts": {"rows": [1, 2]}, "outcome": "fix"}
    checkpoint["channel_versions"] = {"facts": "1", "outcome": "1"}
    saved = await saver.aput(THREAD_CONFIG, checkpoint, {}, {"facts": "1", "outcome": "1"})
    await saver.aput_writes(saved, [("decision", {"verdict": "approve"})], "task-1", "path-1")


async def test_open_checkpointer_round_trips_blobs_and_writes(
    committing_database_url: str,
) -> None:
    async with open_checkpointer(committing_database_url) as saver:
        await _put_checkpoint_with_values_and_writes(saver)

    async with open_checkpointer(committing_database_url) as saver:
        stored = await saver.aget_tuple(THREAD_CONFIG)

    assert stored is not None
    assert stored.checkpoint["channel_values"] == {"facts": {"rows": [1, 2]}, "outcome": "fix"}
    assert stored.pending_writes == [("task-1", "decision", {"verdict": "approve"})]


async def test_open_checkpointer_wrong_password_fails_fast(
    committing_database_url: str,
) -> None:
    url = make_url(committing_database_url).set(password=WRONG_PASSWORD)

    with pytest.raises(CheckpointerUnavailableError) as raised:
        async with open_checkpointer(url.render_as_string(hide_password=False)):
            pass

    assert "password authentication failed" in str(raised.value)
    assert raised.value.__cause__ is None


async def _pool_open_times_out(
    pool: AsyncConnectionPool[Any], wait: bool = False, timeout: float = 30.0
) -> None:
    raise PoolTimeout("pool initialization incomplete after 10 sec")


async def test_open_checkpointer_pool_open_timeout_is_unavailable(
    committing_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AsyncConnectionPool, "open", _pool_open_times_out)

    with pytest.raises(CheckpointerUnavailableError, match="pool") as raised:
        async with open_checkpointer(committing_database_url):
            pass

    assert raised.value.__cause__ is None


async def test_open_checkpointer_cancelled_during_open_closes_pool(
    committing_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[AsyncConnectionPool[Any]] = []
    real_open = AsyncConnectionPool.open

    async def open_then_cancel(
        pool: AsyncConnectionPool[Any], wait: bool = False, timeout: float = 30.0
    ) -> None:
        await real_open(pool, wait=wait, timeout=timeout)
        opened.append(pool)
        raise asyncio.CancelledError

    monkeypatch.setattr(AsyncConnectionPool, "open", open_then_cancel)

    with pytest.raises(asyncio.CancelledError):
        async with open_checkpointer(committing_database_url):
            pass

    assert opened[0].closed


async def test_open_checkpointer_replaces_dropped_connections(
    committing_database_url: str, committing_engine: AsyncEngine
) -> None:
    async with open_checkpointer(committing_database_url) as saver:
        async with committing_engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid()"
                )
            )

        assert await saver.aget_tuple(THREAD_CONFIG) is None


async def test_open_checkpointer_pool_waits_briefly_for_a_connection(
    committing_database_url: str,
) -> None:
    async with open_checkpointer(committing_database_url) as saver:
        pool = saver.conn

    assert isinstance(pool, AsyncConnectionPool)
    assert pool.timeout == CHECKPOINT_POOL_TIMEOUT_SECONDS
