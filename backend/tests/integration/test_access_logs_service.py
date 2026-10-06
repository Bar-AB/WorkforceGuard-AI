import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from app.errors import NotFoundError
from app.security.tenant import TenantContext
from app.services.access_logs import list_access_logs
from app.services.bounds import EventQuery
from tests.factories import insert_access_log, insert_company, insert_employee

START = datetime(2026, 1, 5, tzinfo=UTC)
END = START + timedelta(days=1)


async def test_list_access_logs_returns_window_rows_in_order(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    later = await insert_access_log(
        rollback_conn, company_id, employee_id, START + timedelta(hours=9)
    )
    at_start = await insert_access_log(rollback_conn, company_id, employee_id, START)
    await insert_access_log(rollback_conn, company_id, employee_id, START - timedelta(hours=1))

    page = await list_access_logs(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, limit=200)
    )

    assert [item.id for item in page.items] == [at_start, later]
    assert page.items[0].door == "main"
    assert page.truncated is False


async def test_list_access_logs_other_company_rows_are_excluded(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    own = await insert_access_log(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id), START
    )
    other_company_id = await insert_company(rollback_conn)
    await insert_access_log(
        rollback_conn,
        other_company_id,
        await insert_employee(rollback_conn, other_company_id),
        START,
    )

    page = await list_access_logs(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, limit=200)
    )

    assert [item.id for item in page.items] == [own]


async def test_list_access_logs_filters_by_employee(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    own = await insert_access_log(rollback_conn, company_id, employee_id, START)
    await insert_access_log(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id), START
    )

    page = await list_access_logs(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, employee_id)
    )

    assert [item.id for item in page.items] == [own]


async def test_list_access_logs_limit_truncates(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    first = await insert_access_log(rollback_conn, company_id, employee_id, START)
    second = await insert_access_log(
        rollback_conn, company_id, employee_id, START + timedelta(hours=1)
    )
    await insert_access_log(rollback_conn, company_id, employee_id, START + timedelta(hours=2))

    page = await list_access_logs(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, limit=2)
    )

    assert [item.id for item in page.items] == [first, second]
    assert page.truncated is True


async def test_list_access_logs_excludes_row_at_window_end(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    await insert_access_log(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id), END
    )

    page = await list_access_logs(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, limit=200)
    )

    assert page.items == []


async def test_list_access_logs_unknown_employee_is_not_found(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)

    with pytest.raises(NotFoundError, match="Employee"):
        await list_access_logs(
            rollback_conn, TenantContext(company_id), EventQuery(START, END, uuid.uuid4())
        )
