import uuid

from fastapi import APIRouter

from app.api.deps import Connection, Tenant
from app.services import employees
from app.services.employees import EmployeeDetail

router = APIRouter(prefix="/api/v1/employees", tags=["employees"])


@router.get("/{employee_id}")
async def get_employee(conn: Connection, tenant: Tenant, employee_id: uuid.UUID) -> EmployeeDetail:
    return await employees.get_employee(conn, tenant, employee_id)
