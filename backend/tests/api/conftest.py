from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import get_connection
from app.main import create_app


@pytest.fixture
async def client(rollback_conn: AsyncConnection) -> AsyncIterator[AsyncClient]:
    """API client whose requests all share the test's rolled-back connection."""
    app = create_app()

    async def _test_connection() -> AsyncIterator[AsyncConnection]:
        yield rollback_conn

    app.dependency_overrides[get_connection] = _test_connection
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        yield api
