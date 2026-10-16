import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from app.errors import ForbiddenError
from app.security.tenant import TenantContext
from app.services.users import Approver, require_approver
from tests.factories import insert_company, insert_user


@pytest.mark.parametrize("role", ["admin", "manager"])
async def test_require_approver_admin_and_manager_pass(
    rollback_conn: AsyncConnection, role: str
) -> None:
    company_id = await insert_company(rollback_conn)
    user_id = await insert_user(rollback_conn, company_id, role)

    approver = await require_approver(rollback_conn, TenantContext(company_id), user_id)

    assert approver == Approver(user_id=user_id, role=role)


async def test_require_approver_auditor_is_forbidden(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)
    user_id = await insert_user(rollback_conn, company_id, "auditor")

    with pytest.raises(ForbiddenError, match="not allowed to decide"):
        await require_approver(rollback_conn, TenantContext(company_id), user_id)


async def test_require_approver_unknown_user_is_forbidden(rollback_conn: AsyncConnection) -> None:
    company_id = await insert_company(rollback_conn)

    with pytest.raises(ForbiddenError, match="not allowed to decide"):
        await require_approver(rollback_conn, TenantContext(company_id), uuid.uuid4())


async def test_require_approver_other_company_user_is_forbidden(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    other_company_id = await insert_company(rollback_conn)
    other_user_id = await insert_user(rollback_conn, other_company_id, "admin")

    with pytest.raises(ForbiddenError) as caught:
        await require_approver(rollback_conn, TenantContext(company_id), other_user_id)

    assert str(other_user_id) not in str(caught.value)
