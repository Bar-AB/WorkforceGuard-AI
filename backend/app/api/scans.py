from fastapi import APIRouter

from app.api.deps import LLM, Connection, ExplainLimit, Tenant
from app.graph import scan_graph
from app.services.scans import ScanResult

router = APIRouter(prefix="/api/v1/scans", tags=["scans"])


@router.post("")
async def run_scan(
    conn: Connection, tenant: Tenant, provider: LLM, explain_limit: ExplainLimit
) -> ScanResult:
    return await scan_graph.run_scan(conn, tenant, provider, explain_limit)
