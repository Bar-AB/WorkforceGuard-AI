import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AttendanceEvent
from app.security.tenant import TenantContext
from app.services.bounds import EventQuery, read_event_window


class AttendanceEventItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    source: str
    device_id: str | None
    note: str | None


class AttendanceEventsPage(BaseModel):
    items: list[AttendanceEventItem]
    truncated: bool


_COLUMNS = (
    AttendanceEvent.id,
    AttendanceEvent.employee_id,
    AttendanceEvent.event_type,
    AttendanceEvent.occurred_at,
    AttendanceEvent.source,
    AttendanceEvent.device_id,
    AttendanceEvent.note,
)


async def list_attendance_events(
    conn: AsyncConnection,
    tenant: TenantContext,
    query: EventQuery,
) -> AttendanceEventsPage:
    found = await read_event_window(
        conn,
        tenant,
        _COLUMNS,
        AttendanceEvent.occurred_at,
        query,
    )
    items = [AttendanceEventItem.model_validate(row) for row in found.rows]
    return AttendanceEventsPage(items=items, truncated=found.truncated)
