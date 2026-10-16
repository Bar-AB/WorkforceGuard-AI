import asyncio
import uuid
from pathlib import Path
from typing import Final

from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

LOCK_WAIT_ATTEMPTS: Final = 100
LOCK_WAIT_POLL_SECONDS: Final = 0.05

SOURCE_TABLES = [
    "companies",
    "employees",
    "users",
    "shifts",
    "attendance_events",
    "access_logs",
    "payroll_runs",
    "overtime_policies",
]


def alembic_config(database_url: str) -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def _run_admin_sql(statement: str) -> None:
    admin_url = make_url(Settings().database_url).set(database="postgres")
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(statement))
    finally:
        await engine.dispose()


def create_empty_database() -> str:
    name = f"wg_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_run_admin_sql(f'CREATE DATABASE "{name}"'))
    url = make_url(Settings().database_url).set(database=name)
    return url.render_as_string(hide_password=False)


def drop_database(database_url: str) -> None:
    name = make_url(database_url).database
    asyncio.run(_run_admin_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


async def wait_until_lock_wait(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        for _ in range(LOCK_WAIT_ATTEMPTS):
            waiting = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
            )
            if waiting:
                return
            await conn.rollback()
            await asyncio.sleep(LOCK_WAIT_POLL_SECONDS)
    raise AssertionError("no session ever waited for a lock")
