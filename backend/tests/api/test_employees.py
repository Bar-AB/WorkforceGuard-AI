import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import insert_company, insert_employee


async def test_get_employee_returns_profile(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)

    response = await client.get(
        f"/api/v1/employees/{employee_id}", headers={"X-Company-Id": str(company_id)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(employee_id)
    assert body["full_name"] == "Test Person"
    assert body["hired_on"] == "2026-01-01"
    assert body["terminated_on"] is None


async def test_get_employee_of_other_company_returns_404(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_a = await insert_company(rollback_conn)
    company_b = await insert_company(rollback_conn)
    employee_of_b = await insert_employee(rollback_conn, company_b)

    response = await client.get(
        f"/api/v1/employees/{employee_of_b}", headers={"X-Company-Id": str(company_a)}
    )

    assert response.status_code == 404


async def test_get_unknown_employee_returns_404(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    missing_employee_id = uuid.uuid4()

    response = await client.get(
        f"/api/v1/employees/{missing_employee_id}", headers={"X-Company-Id": str(company_id)}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": f"Employee {missing_employee_id} not found."}
