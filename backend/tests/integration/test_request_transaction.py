"""The real per-request transaction: committed before the response, rolled back on error."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import Connection
from app.db.models import AuditLog, Company, Finding
from app.errors import ConflictError
from app.main import create_app
from tests.db_helpers import alembic_config
from tests.factories import (
    insert_company,
    insert_employee,
    insert_overtime_policy,
    insert_worked_shift,
)

WRITE_THEN_CONFLICT = "/test/write-then-conflict"
# A Tuesday, so a 13 h day breaks only the daily overtime limit.
TUESDAY_8AM = datetime(2026, 1, 6, 8, tzinfo=UTC)


@pytest.fixture
def committing_database_url(empty_database_url: str) -> str:
    """A throwaway migrated DB, because these tests commit for real."""
    command.upgrade(alembic_config(empty_database_url), "head")
    return empty_database_url


@pytest.fixture
async def committing_engine(committing_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(committing_database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


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
    """Client on the real app and real `get_connection`, reporting crashes as HTTP 500."""
    monkeypatch.setenv("DATABASE_URL", committing_database_url)
    app = _app_with_failing_write_route()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _company_with_long_day(engine: AsyncEngine) -> uuid.UUID:
    async with engine.begin() as conn:
        company_id = await insert_company(conn)
        employee_id = await insert_employee(conn, company_id)
        await insert_overtime_policy(conn, company_id)
        await insert_worked_shift(conn, company_id, employee_id, TUESDAY_8AM, hours=13)
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
    company_id = await _company_with_long_day(committing_engine)

    response = await api.post("/api/v1/scans", headers={"X-Company-Id": str(company_id)})

    assert response.status_code == 200, response.text
    assert await _count(committing_engine, Finding) == 1


async def test_scan_whose_commit_fails_is_not_reported_as_success(
    api: AsyncClient, committing_engine: AsyncEngine
) -> None:
    company_id = await _company_with_long_day(committing_engine)
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
