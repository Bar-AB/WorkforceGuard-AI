import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, Connection, inspect
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Base
from tests.db_helpers import alembic_config

EXPECTED_TABLES = {
    "companies",
    "employees",
    "users",
    "shifts",
    "attendance_events",
    "access_logs",
    "payroll_runs",
    "overtime_policies",
    "findings",
    "proposed_corrections",
    "audit_log",
    "anomaly_labels",
}


def _app_tables(sync_conn: Connection) -> set[str]:
    return set(inspect(sync_conn).get_table_names()) - {"alembic_version"}


def _schema_diff(sync_conn: Connection) -> list[object]:
    context = MigrationContext.configure(sync_conn, opts={"compare_server_default": True})
    return list(compare_metadata(context, Base.metadata))


def _db_check_constraints(sync_conn: Connection) -> set[str]:
    inspector = inspect(sync_conn)
    return {
        check["name"]
        for table in _app_tables(sync_conn)
        for check in inspector.get_check_constraints(table)
        if check["name"] is not None
    }


def _model_check_constraints() -> set[str]:
    return {
        str(constraint.name)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


async def _tables_in(database_url: str) -> set[str]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_app_tables)
    finally:
        await engine.dispose()


async def test_upgrade_head_on_empty_db_creates_all_tables(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.connect() as conn:
        tables = await conn.run_sync(_app_tables)

    assert tables == EXPECTED_TABLES


def test_downgrade_base_after_upgrade_removes_all_tables(empty_database_url: str) -> None:
    config = alembic_config(empty_database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    assert asyncio.run(_tables_in(empty_database_url)) == set()


def test_upgrade_after_downgrade_recreates_all_tables(empty_database_url: str) -> None:
    config = alembic_config(empty_database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert asyncio.run(_tables_in(empty_database_url)) == EXPECTED_TABLES


async def test_models_match_migrations_with_no_drift(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.connect() as conn:
        diff = await conn.run_sync(_schema_diff)

    assert diff == []


def _findings_index_columns(sync_conn: Connection) -> list[list[str | None]]:
    return [index["column_names"] for index in inspect(sync_conn).get_indexes("findings")]


async def test_findings_newest_first_listing_has_index(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.connect() as conn:
        indexed = await conn.run_sync(_findings_index_columns)

    assert ["company_id", "detected_at", "id"] in indexed


async def test_check_constraints_in_db_match_models(migrated_engine: AsyncEngine) -> None:
    async with migrated_engine.connect() as conn:
        db_checks = await conn.run_sync(_db_check_constraints)

    assert db_checks == _model_check_constraints()
