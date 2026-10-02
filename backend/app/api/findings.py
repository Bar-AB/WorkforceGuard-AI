import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import Connection, Tenant
from app.services import findings
from app.services.findings import MAX_PAGE_SIZE, FindingDetail, FindingsPage

router = APIRouter(prefix="/api/v1/findings", tags=["findings"])


@router.get("")
async def list_findings(
    conn: Connection,
    tenant: Tenant,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    cursor: str | None = None,
) -> FindingsPage:
    return await findings.list_findings(conn, tenant, limit, cursor)


@router.get("/{finding_id}")
async def get_finding(conn: Connection, tenant: Tenant, finding_id: uuid.UUID) -> FindingDetail:
    return await findings.get_finding(conn, tenant, finding_id)
