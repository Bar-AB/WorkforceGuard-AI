import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.errors import UnauthenticatedError
from app.graph.correction_graph import CorrectionGraph
from app.llm.provider import LLMProvider
from app.security.tenant import TenantContext
from app.services import companies, users
from app.services.users import Approver


async def get_connection(request: Request) -> AsyncIterator[AsyncConnection]:
    engine: AsyncEngine = request.app.state.engine
    async with engine.begin() as conn:
        yield conn


Connection = Annotated[AsyncConnection, Depends(get_connection, scope="function")]


async def get_tenant(
    x_company_id: Annotated[uuid.UUID, Header()],
    conn: Connection,
) -> TenantContext:
    tenant = TenantContext(company_id=x_company_id)
    await companies.require_company(conn, tenant)
    return tenant


def get_correction_graph(request: Request) -> CorrectionGraph:
    graph: CorrectionGraph = request.app.state.correction_graph
    return graph


def get_llm_provider(request: Request) -> LLMProvider:
    provider: LLMProvider = request.app.state.llm_provider
    return provider


def get_suggest_timeout(request: Request) -> float:
    timeout_seconds: float = request.app.state.suggest_timeout_seconds
    return timeout_seconds


def get_explain_limit(request: Request) -> int:
    limit: int = request.app.state.explain_limit
    return limit


Tenant = Annotated[TenantContext, Depends(get_tenant)]


async def get_approver(
    conn: Connection,
    tenant: Tenant,
    x_user_id: Annotated[uuid.UUID | None, Header()] = None,
) -> Approver:
    if x_user_id is None:
        raise UnauthenticatedError("The X-User-Id header is required to decide corrections.")
    return await users.require_approver(conn, tenant, x_user_id)


LLM = Annotated[LLMProvider, Depends(get_llm_provider)]
ExplainLimit = Annotated[int, Depends(get_explain_limit)]
SuggestTimeout = Annotated[float, Depends(get_suggest_timeout)]
ApproverDep = Annotated[Approver, Depends(get_approver)]
CorrectionGraphDep = Annotated[CorrectionGraph, Depends(get_correction_graph)]
