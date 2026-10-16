import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import User
from app.errors import ForbiddenError
from app.security.tenant import TenantContext

APPROVER_ROLES: Final = frozenset({"admin", "manager"})


@dataclass(frozen=True)
class Approver:
    user_id: uuid.UUID
    role: str


async def require_approver(
    conn: AsyncConnection, tenant: TenantContext, user_id: uuid.UUID
) -> Approver:
    role = await conn.scalar(
        select(User.role).where(User.company_id == tenant.company_id, User.id == user_id)
    )
    if role is None or role not in APPROVER_ROLES:
        raise ForbiddenError("This user is not allowed to decide corrections.")
    return Approver(user_id=user_id, role=role)
