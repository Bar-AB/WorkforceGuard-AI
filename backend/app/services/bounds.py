import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import InstrumentedAttribute

from app.errors import InvalidInputError
from app.security.tenant import TenantContext
from app.services.employees import require_employee

EARLIEST_DAY = date(1970, 1, 1)
LATEST_DAY = date(2100, 12, 31)
MAX_EVENT_WINDOW = timedelta(days=31)
MAX_EVENT_ROWS = 500
DEFAULT_EVENT_ROWS = 200


@dataclass(frozen=True)
class EventQuery:
    start: datetime
    end: datetime
    employee_id: uuid.UUID | None = None
    limit: int = DEFAULT_EVENT_ROWS

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise InvalidInputError("start and end need a time zone.")
        check_supported_day("start", self.start.date())
        check_supported_day("end", self.end.date())
        if self.end <= self.start:
            raise InvalidInputError("end must be after start.")
        if self.end - self.start > MAX_EVENT_WINDOW:
            raise InvalidInputError(f"The window can span at most {MAX_EVENT_WINDOW.days} days.")
        if not 1 <= self.limit <= MAX_EVENT_ROWS:
            raise InvalidInputError(f"limit must be between 1 and {MAX_EVENT_ROWS}.")


def check_supported_day(name: str, day: date) -> None:
    if not EARLIEST_DAY <= day <= LATEST_DAY:
        raise InvalidInputError(
            f"{name} must fall between {EARLIEST_DAY.year} and {LATEST_DAY.year}."
        )


@dataclass(frozen=True)
class EventRows:
    rows: Sequence[Row[*tuple[Any, ...]]]
    truncated: bool


async def read_event_window(
    conn: AsyncConnection,
    tenant: TenantContext,
    columns: tuple[InstrumentedAttribute[Any], ...],
    time_column: InstrumentedAttribute[datetime],
    query: EventQuery,
) -> EventRows:
    table = time_column.table
    statement = (
        select(*columns)
        .where(
            table.c.company_id == tenant.company_id,
            time_column >= query.start,
            time_column < query.end,
        )
        .order_by(time_column, table.c.id)
        .limit(query.limit + 1)
    )
    if query.employee_id is not None:
        statement = statement.where(table.c.employee_id == query.employee_id)
    rows = (await conn.execute(statement)).all()
    if not rows and query.employee_id is not None:
        await require_employee(conn, tenant, query.employee_id)
    return EventRows(rows=rows[: query.limit], truncated=len(rows) > query.limit)
