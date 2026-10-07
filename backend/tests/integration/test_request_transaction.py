import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import Connection, get_explain_limit, get_llm_provider
from app.db.models import AuditLog, Company, Finding
from app.errors import ConflictError
from app.llm.provider import OllamaProvider
from app.main import create_app
from tests.factories import insert_company, insert_company_with_long_day
from tests.fake_llm import TEST_EXPLAIN_LIMIT, FakeLLM

WRITE_THEN_CONFLICT = "/test/write-then-conflict"


def _app_with_failing_write_route() -> FastAPI:
    app = create_app()

    @app.post(WRITE_THEN_CONFLICT)
    async def write_then_conflict(conn: Connection) -> None:
        await insert_company(conn)
        raise ConflictError("Refused after writing.")

    return app


@pytest.fixture
async def api(
    committing_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("DATABASE_URL", committing_database_url)
    app = _app_with_failing_write_route()
    fake = FakeLLM()
    app.dependency_overrides[get_llm_provider] = lambda: fake
    app.dependency_overrides[get_explain_limit] = lambda: TEST_EXPLAIN_LIMIT
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _committed_company_with_long_day(engine: AsyncEngine) -> uuid.UUID:
    async with engine.begin() as conn:
        company_id, _ = await insert_company_with_long_day(conn)
    return company_id


async def _refuse_audit_rows_at_commit(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE FUNCTION refuse_commit() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'commit refused'; END $$"
            )
        )
        await conn.execute(
            text(
                "CREATE CONSTRAINT TRIGGER refuse_audit_at_commit AFTER INSERT ON audit_log "
                "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION refuse_commit()"
            )
        )


async def _count(engine: AsyncEngine, model: type[Finding | AuditLog | Company]) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(select(func.count()).select_from(model))).scalar_one()


async def test_successful_scan_is_committed(
    api: AsyncClient, committing_engine: AsyncEngine
) -> None:
    company_id = await _committed_company_with_long_day(committing_engine)

    response = await api.post("/api/v1/scans", headers={"X-Company-Id": str(company_id)})

    assert response.status_code == 200, response.text
    assert await _count(committing_engine, Finding) == 1


async def test_scan_whose_commit_fails_is_not_reported_as_success(
    api: AsyncClient, committing_engine: AsyncEngine
) -> None:
    company_id = await _committed_company_with_long_day(committing_engine)
    await _refuse_audit_rows_at_commit(committing_engine)

    response = await api.post("/api/v1/scans", headers={"X-Company-Id": str(company_id)})

    assert response.status_code == 500
    assert await _count(committing_engine, Finding) == 0


async def test_domain_error_after_a_write_leaves_nothing_written(
    api: AsyncClient, committing_engine: AsyncEngine
) -> None:
    response = await api.post(WRITE_THEN_CONFLICT)

    assert response.status_code == 409
    assert await _count(committing_engine, Company) == 0


async def test_scan_of_unknown_company_leaves_nothing_written(
    api: AsyncClient, committing_engine: AsyncEngine
) -> None:
    response = await api.post("/api/v1/scans", headers={"X-Company-Id": str(uuid.uuid4())})

    assert response.status_code == 404
    assert await _count(committing_engine, AuditLog) == 0


async def test_lifespan_provides_llm_provider_and_limit_from_settings(
    committing_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", committing_database_url)
    monkeypatch.setenv("LLM_MODEL", "qwen3:test")
    monkeypatch.setenv("LLM_EXPLAIN_LIMIT", "3")
    app = create_app()

    async with app.router.lifespan_context(app):
        provider = app.state.llm_provider
        assert isinstance(provider, OllamaProvider)
        assert provider.model == "qwen3:test"
        assert app.state.explain_limit == 3
