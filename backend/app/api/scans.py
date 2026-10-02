from fastapi import APIRouter

from app.api.deps import Connection, Tenant
from app.services import scans
from app.services.scans import ScanResult

router = APIRouter(prefix="/api/v1/scans", tags=["scans"])


@router.post("")
async def run_scan(conn: Connection, tenant: Tenant) -> ScanResult:
    return await scans.run_scan(conn, tenant)
