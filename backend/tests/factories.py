import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def insert_company(conn: AsyncConnection) -> uuid.UUID:
    company_id = uuid.uuid4()
    await conn.execute(
        text("INSERT INTO companies (id, name) VALUES (:id, :name)"),
        {"id": company_id, "name": f"Company {company_id}"},
    )
    return company_id


async def insert_employee(conn: AsyncConnection, company_id: uuid.UUID) -> uuid.UUID:
    employee_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO employees (id, company_id, employee_number, full_name, hired_on) "
            "VALUES (:id, :company_id, :number, 'Test Person', '2026-01-01')"
        ),
        {"id": employee_id, "company_id": company_id, "number": employee_id.hex[:8]},
    )
    return employee_id


async def insert_finding(
    conn: AsyncConnection, company_id: uuid.UUID, employee_id: uuid.UUID
) -> uuid.UUID:
    result = await conn.execute(
        text(
            "INSERT INTO findings (company_id, employee_id, rule_id, rule_version, severity, "
            "summary) VALUES (:c, :e, 'overtime', 'v1', 'high', 'Too many hours') RETURNING id"
        ),
        {"c": company_id, "e": employee_id},
    )
    finding_id: uuid.UUID = result.scalar_one()
    return finding_id
