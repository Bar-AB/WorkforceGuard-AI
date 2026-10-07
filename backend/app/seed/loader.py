import uuid

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.models import Base
from app.seed.generator import Dataset


async def _delete_companies(conn: AsyncConnection, company_ids: list[uuid.UUID]) -> None:
    for table in reversed(Base.metadata.sorted_tables):
        if "company_id" in table.c:
            await conn.execute(delete(table).where(table.c.company_id.in_(company_ids)))
    companies = Base.metadata.tables["companies"]
    await conn.execute(delete(companies).where(companies.c.id.in_(company_ids)))


async def load_dataset(conn: AsyncConnection, dataset: Dataset) -> None:
    await _delete_companies(conn, dataset.company_ids())
    for table, rows in dataset.tables():
        if rows:
            await conn.execute(insert(table), rows)


async def seed_database(database_url: str, dataset: Dataset) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await load_dataset(conn, dataset)
    finally:
        await engine.dispose()
