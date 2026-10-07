from collections.abc import AsyncIterator

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.db_helpers import alembic_config


@pytest.fixture
def committing_database_url(empty_database_url: str) -> str:
    command.upgrade(alembic_config(empty_database_url), "head")
    return empty_database_url


@pytest.fixture
async def committing_engine(committing_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(committing_database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()
