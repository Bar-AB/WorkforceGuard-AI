import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.llm.provider import LLMProvider
from app.security.tenant import TenantContext


async def get_connection(request: Request) -> AsyncIterator[AsyncConnection]:
    engine: AsyncEngine = request.app.state.engine
    async with engine.begin() as conn:
        yield conn


def get_tenant(x_company_id: Annotated[uuid.UUID, Header()]) -> TenantContext:
    return TenantContext(company_id=x_company_id)


def get_llm_provider(request: Request) -> LLMProvider:
    provider: LLMProvider = request.app.state.llm_provider
    return provider


def get_explain_limit(request: Request) -> int:
    limit: int = request.app.state.explain_limit
    return limit


Connection = Annotated[AsyncConnection, Depends(get_connection, scope="function")]
Tenant = Annotated[TenantContext, Depends(get_tenant)]
LLM = Annotated[LLMProvider, Depends(get_llm_provider)]
ExplainLimit = Annotated[int, Depends(get_explain_limit)]
