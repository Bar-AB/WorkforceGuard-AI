import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import AuditLog
from mcp_server.db import reader_transaction
from tests.factories import insert_company


class _AbortError(Exception):
    pass


async def test_reader_transaction_current_user_is_mcp_reader(migrated_engine: AsyncEngine) -> None:
    async with reader_transaction(migrated_engine) as conn:
        current_user = await conn.scalar(text("SELECT current_user"))

    assert current_user == "mcp_reader"


async def test_reader_transaction_update_source_table_is_denied(
    migrated_engine: AsyncEngine,
) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        async with reader_transaction(migrated_engine) as conn:
            await conn.execute(text("UPDATE shifts SET id = id"))


async def test_reader_transaction_error_rolls_back(tool_engine: AsyncEngine) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    with pytest.raises(_AbortError):
        async with reader_transaction(tool_engine) as conn:
            await conn.execute(
                text(
                    "INSERT INTO audit_log (company_id, action, entity_type) "
                    "VALUES (:c, 'test.abort', 'test')"
                ),
                {"c": company_id},
            )
            raise _AbortError

    async with tool_engine.connect() as check:
        count = await check.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.company_id == company_id)
        )
    assert count == 0
