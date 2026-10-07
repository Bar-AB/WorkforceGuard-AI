from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import get_connection, get_explain_limit, get_llm_provider
from app.main import create_app
from tests.fake_llm import TEST_EXPLAIN_LIMIT, FakeLLM


@pytest.fixture
def fake_llm(request: pytest.FixtureRequest) -> FakeLLM:
    replies: tuple[str | Exception, ...] = getattr(request, "param", ())
    return FakeLLM(*replies)


@pytest.fixture
async def client(rollback_conn: AsyncConnection, fake_llm: FakeLLM) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _test_connection() -> AsyncIterator[AsyncConnection]:
        yield rollback_conn

    app.dependency_overrides[get_connection] = _test_connection
    app.dependency_overrides[get_llm_provider] = lambda: fake_llm
    app.dependency_overrides[get_explain_limit] = lambda: TEST_EXPLAIN_LIMIT
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        yield api
