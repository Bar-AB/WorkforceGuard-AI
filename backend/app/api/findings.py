import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import Connection, Tenant
from app.services import findings, scans
from app.services.findings import (
    MAX_PAGE_SIZE,
    FindingDetail,
    FindingFilters,
    FindingsPage,
    Severity,
)

router = APIRouter(prefix="/api/v1/findings", tags=["findings"])


def finding_filters(
    rule_id: str | None = None,
    severity: Severity | None = None,
    occurred_from: date | None = None,
    occurred_to: date | None = None,
) -> FindingFilters:
    if rule_id is not None:
        scans.require_known_rule(rule_id)
    return FindingFilters(rule_id, severity, occurred_from, occurred_to)


@router.get("")
async def list_findings(
    conn: Connection,
    tenant: Tenant,
    filters: Annotated[FindingFilters, Depends(finding_filters)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    cursor: str | None = None,
) -> FindingsPage:
    return await findings.list_findings(conn, tenant, filters, limit, cursor)


@router.get("/{finding_id}")
async def get_finding(conn: Connection, tenant: Tenant, finding_id: uuid.UUID) -> FindingDetail:
    return await findings.get_finding(conn, tenant, finding_id)
