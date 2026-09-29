from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.db_helpers import alembic_config, create_empty_database, drop_database


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
