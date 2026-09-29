from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import Row, func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import AnomalyLabel, AttendanceEvent
from app.seed.config import SeedConfig
from app.seed.generator import Dataset, generate_dataset
from app.seed.loader import load_dataset, seed_database
from app.seed.vocab import INJECTION_NOTES
from tests.db_helpers import alembic_config
from tests.factories import insert_company, insert_employee

SMALL = SeedConfig(
    employees_per_company=15,
    days=14,
    overtime_breaches_per_company=2,
    buddy_pairs_per_company=1,
    buddy_days_per_pair=2,
    off_shift_accesses_per_company=1,
    injected_notes_per_company=2,
)

Snapshot = dict[str, list[Row[tuple[object, ...]]]]


@pytest.fixture
def fresh_database_url(empty_database_url: str) -> Iterator[str]:
    command.upgrade(alembic_config(empty_database_url), "head")
    yield empty_database_url


async def _snapshot(conn: AsyncConnection, data: Dataset) -> Snapshot:
    snapshot: Snapshot = {}
    for table, _ in data.tables():
        result = await conn.execute(select(table).order_by(table.c.id))
        snapshot[table.name] = list(result.all())
    return snapshot


async def _snapshot_url(database_url: str, data: Dataset) -> Snapshot:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await _snapshot(conn, data)
    finally:
        await engine.dispose()


async def test_seeding_twice_with_same_seed_gives_same_rows(fresh_database_url: str) -> None:
    data = generate_dataset(SMALL)

    await seed_database(fresh_database_url, data)
    first = await _snapshot_url(fresh_database_url, data)
    await seed_database(fresh_database_url, generate_dataset(SMALL))
    second = await _snapshot_url(fresh_database_url, data)

    assert first == second
    for table, rows in data.tables():
        assert len(first[table.name]) == len(rows)


async def test_loaded_label_counts_match_config(rollback_conn: AsyncConnection) -> None:
    await load_dataset(rollback_conn, generate_dataset(SMALL))

    result = await rollback_conn.execute(
        select(AnomalyLabel.anomaly_type, func.count()).group_by(AnomalyLabel.anomaly_type)
    )

    assert dict(result.all()) == {
        "overtime_breach": 2 * 2,
        "buddy_punching": 2 * 2,
        "off_shift_access": 1 * 2,
    }


async def test_loaded_injection_notes_match_config(rollback_conn: AsyncConnection) -> None:
    await load_dataset(rollback_conn, generate_dataset(SMALL))

    count = await rollback_conn.scalar(
        select(func.count()).where(AttendanceEvent.note.in_(INJECTION_NOTES))
    )

    assert count == 2 * 2


async def test_reseeding_keeps_other_companies(rollback_conn: AsyncConnection) -> None:
    other_company = await insert_company(rollback_conn)
    other_employee = await insert_employee(rollback_conn, other_company)

    await load_dataset(rollback_conn, generate_dataset(SMALL))
    await load_dataset(rollback_conn, generate_dataset(SMALL))

    remaining = await rollback_conn.scalar(
        text("SELECT count(*) FROM employees WHERE id = :id AND company_id = :company"),
        {"id": other_employee, "company": other_company},
    )
    assert remaining == 1
