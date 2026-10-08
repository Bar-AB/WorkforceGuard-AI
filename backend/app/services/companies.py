from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Company
from app.errors import NotFoundError
from app.security.tenant import TenantContext


async def require_company(conn: AsyncConnection, tenant: TenantContext) -> None:
    found = await conn.scalar(select(Company.id).where(Company.id == tenant.company_id))
    if found is None:
        raise NotFoundError("Unknown company.")
