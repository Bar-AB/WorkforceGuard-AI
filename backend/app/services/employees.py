import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Employee
from app.errors import NotFoundError
from app.security.tenant import TenantContext


class EmployeeDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_number: str
    full_name: str
    email: str | None
    department: str | None
    manager_id: uuid.UUID | None
    hired_on: date
    terminated_on: date | None


async def get_employee(
    conn: AsyncConnection, tenant: TenantContext, employee_id: uuid.UUID
) -> EmployeeDetail:
    row = (
        await conn.execute(
            select(
                Employee.id,
                Employee.employee_number,
                Employee.full_name,
                Employee.email,
                Employee.department,
                Employee.manager_id,
                Employee.hired_on,
                Employee.terminated_on,
            ).where(Employee.company_id == tenant.company_id, Employee.id == employee_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"Employee {employee_id} not found.")
    return EmployeeDetail.model_validate(row)


async def require_employee(
    conn: AsyncConnection, tenant: TenantContext, employee_id: uuid.UUID
) -> None:
    found = await conn.scalar(
        select(Employee.id).where(
            Employee.company_id == tenant.company_id, Employee.id == employee_id
        )
    )
    if found is None:
        raise NotFoundError(f"Employee {employee_id} not found.")
