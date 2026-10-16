import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import LLM, ApproverDep, Connection, CorrectionGraphDep, SuggestTimeout, Tenant
from app.graph.correction_graph import Decision, decide_correction, suggest_correction
from app.services import correction_decisions
from app.services.correction_decisions import CorrectionView, SuggestOutcome

router = APIRouter(tags=["corrections"])


@router.get("/api/v1/findings/{finding_id}/corrections")
async def list_corrections(
    conn: Connection, tenant: Tenant, finding_id: uuid.UUID
) -> list[CorrectionView]:
    return await correction_decisions.list_corrections(conn, tenant, finding_id)


@router.post("/api/v1/findings/{finding_id}/corrections", status_code=status.HTTP_201_CREATED)
async def suggest(  # noqa: PLR0913 - FastAPI injects each dependency as one parameter
    conn: Connection,
    tenant: Tenant,
    graph: CorrectionGraphDep,
    provider: LLM,
    finding_id: uuid.UUID,
    *,
    timeout_seconds: SuggestTimeout,
    response: Response,
) -> SuggestOutcome:
    outcome = await suggest_correction(
        graph, conn, tenant, provider, finding_id, timeout_seconds=timeout_seconds
    )
    if outcome.proposal is None:
        response.status_code = status.HTTP_200_OK
    return outcome


@router.post("/api/v1/corrections/{correction_id}/approve")
async def approve(
    conn: Connection,
    tenant: Tenant,
    approver: ApproverDep,
    graph: CorrectionGraphDep,
    correction_id: uuid.UUID,
) -> CorrectionView:
    decision = Decision(verdict="approve", approver=approver)
    return await decide_correction(graph, conn, tenant, correction_id, decision)


@router.post("/api/v1/corrections/{correction_id}/reject")
async def reject(
    conn: Connection,
    tenant: Tenant,
    approver: ApproverDep,
    graph: CorrectionGraphDep,
    correction_id: uuid.UUID,
) -> CorrectionView:
    decision = Decision(verdict="reject", approver=approver)
    return await decide_correction(graph, conn, tenant, correction_id, decision)
