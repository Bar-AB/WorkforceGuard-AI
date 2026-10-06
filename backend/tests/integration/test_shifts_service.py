import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from app.errors import NotFoundError
from app.security.tenant import TenantContext
from app.services.bounds import EventQuery
from app.services.shifts import list_shifts
from tests.factories import insert_company, insert_employee, insert_shift

START = datetime(2026, 1, 5, tzinfo=UTC)
END = START + timedelta(days=1)


async def test_list_shifts_returns_window_rows_in_order(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    night = await insert_shift(rollback_conn, company_id, employee_id, END - timedelta(hours=1), 8)
    at_start = await insert_shift(rollback_conn, company_id, employee_id, START, 8)
    await insert_shift(rollback_conn, company_id, employee_id, START - timedelta(hours=2), 4)

    page = await list_shifts(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, limit=200)
    )

    assert [item.id for item in page.items] == [at_start, night]
    assert page.items[1].ends_at == END + timedelta(hours=7)
    assert page.truncated is False


async def test_list_shifts_other_company_rows_are_excluded(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    own = await insert_shift(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id), START, 8
    )
    other_company_id = await insert_company(rollback_conn)
    await insert_shift(
        rollback_conn,
        other_company_id,
        await insert_employee(rollback_conn, other_company_id),
        START,
        8,
    )

    page = await list_shifts(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, limit=200)
    )

    assert [item.id for item in page.items] == [own]


async def test_list_shifts_filters_by_employee(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    own = await insert_shift(rollback_conn, company_id, employee_id, START, 8)
    await insert_shift(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id), START, 8
    )

    page = await list_shifts(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, employee_id, 200)
    )

    assert [item.id for item in page.items] == [own]


async def test_list_shifts_limit_truncates(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    first = await insert_shift(rollback_conn, company_id, employee_id, START, 1)
    second = await insert_shift(
        rollback_conn, company_id, employee_id, START + timedelta(hours=1), 1
    )
    await insert_shift(rollback_conn, company_id, employee_id, START + timedelta(hours=2), 1)

    page = await list_shifts(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, limit=2)
    )

    assert [item.id for item in page.items] == [first, second]
    assert page.truncated is True


async def test_list_shifts_excludes_row_at_window_end(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    await insert_shift(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id), END, 8
    )

    page = await list_shifts(
        rollback_conn, TenantContext(company_id), EventQuery(START, END, limit=200)
    )

    assert page.items == []


async def test_list_shifts_unknown_employee_is_not_found(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)

    with pytest.raises(NotFoundError, match="Employee"):
        await list_shifts(
            rollback_conn, TenantContext(company_id), EventQuery(START, END, uuid.uuid4())
        )
