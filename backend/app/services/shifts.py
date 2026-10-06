import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Shift
from app.security.tenant import TenantContext
from app.services.bounds import EventQuery, read_event_window


class ShiftItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime


class ShiftsPage(BaseModel):
    items: list[ShiftItem]
    truncated: bool


_COLUMNS = (
    Shift.id,
    Shift.employee_id,
    Shift.starts_at,
    Shift.ends_at,
)


async def list_shifts(
    conn: AsyncConnection,
    tenant: TenantContext,
    query: EventQuery,
) -> ShiftsPage:
    found = await read_event_window(
        conn,
        tenant,
        _COLUMNS,
        Shift.starts_at,
        query,
    )
    items = [ShiftItem.model_validate(row) for row in found.rows]
    return ShiftsPage(items=items, truncated=found.truncated)
