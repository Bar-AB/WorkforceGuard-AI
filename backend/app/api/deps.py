import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.llm.provider import LLMProvider
from app.security.tenant import TenantContext
from app.services import companies


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


def get_llm_provider(request: Request) -> LLMProvider:
    provider: LLMProvider = request.app.state.llm_provider
    return provider


def get_explain_limit(request: Request) -> int:
    limit: int = request.app.state.explain_limit
    return limit


Tenant = Annotated[TenantContext, Depends(get_tenant)]
LLM = Annotated[LLMProvider, Depends(get_llm_provider)]
ExplainLimit = Annotated[int, Depends(get_explain_limit)]
