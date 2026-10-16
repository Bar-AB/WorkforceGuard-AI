from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import (
    get_connection,
    get_correction_graph,
    get_explain_limit,
    get_llm_provider,
    get_suggest_timeout,
)
from app.graph.correction_graph import build_correction_graph
from app.main import create_app
from tests.fake_llm import TEST_EXPLAIN_LIMIT, TEST_SUGGEST_TIMEOUT_SECONDS, FakeLLM


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
    app.dependency_overrides[get_suggest_timeout] = lambda: TEST_SUGGEST_TIMEOUT_SECONDS
    correction_graph = build_correction_graph(InMemorySaver())
    app.dependency_overrides[get_correction_graph] = lambda: correction_graph
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as api:
        yield api
