import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AccessLog
from app.security.tenant import TenantContext
from app.services.bounds import EventQuery, read_event_window


class AccessLogItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    door: str
    direction: str
    granted: bool
    occurred_at: datetime


class AccessLogsPage(BaseModel):
    items: list[AccessLogItem]
    truncated: bool


_COLUMNS = (
    AccessLog.id,
    AccessLog.employee_id,
    AccessLog.door,
    AccessLog.direction,
    AccessLog.granted,
    AccessLog.occurred_at,
)


async def list_access_logs(
    conn: AsyncConnection,
    tenant: TenantContext,
    query: EventQuery,
) -> AccessLogsPage:
    found = await read_event_window(
        conn,
        tenant,
        _COLUMNS,
        AccessLog.occurred_at,
        query,
    )
    items = [AccessLogItem.model_validate(row) for row in found.rows]
    return AccessLogsPage(items=items, truncated=found.truncated)
