import base64
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import insert_company, insert_employee, insert_finding


def _as(company_id: uuid.UUID) -> dict[str, str]:
    return {"X-Company-Id": str(company_id)}


async def test_list_findings_returns_only_own_company(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_a = await insert_company(rollback_conn)
    company_b = await insert_company(rollback_conn)
    own = await insert_finding(
        rollback_conn, company_a, await insert_employee(rollback_conn, company_a)
    )
    await insert_finding(rollback_conn, company_b, await insert_employee(rollback_conn, company_b))

    response = await client.get("/api/v1/findings", headers=_as(company_a))

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(own)]
    assert body["next_cursor"] is None
    assert "evidence" not in body["items"][0]


async def test_list_findings_pages_with_cursor_visit_every_finding_once(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    created = {str(await insert_finding(rollback_conn, company_id, employee_id)) for _ in range(5)}

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
        response = await client.get("/api/v1/findings", headers=_as(company_id), params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen += [item["id"] for item in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert sorted(seen) == sorted(created)


async def test_list_findings_bad_cursor_returns_422(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/findings", headers=_as(uuid.uuid4()), params={"cursor": "not-a-cursor"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "detected_at",
    [
        pytest.param("2026-01-06T08:00:00", id="naive"),
        pytest.param("0001-01-01T00:00:00+14:00", id="before-utc-min"),
    ],
)
async def test_list_findings_cursor_without_valid_utc_time_returns_422(
    client: AsyncClient, detected_at: str
) -> None:
    raw = f"{detected_at}|{uuid.uuid4()}"
    cursor = base64.urlsafe_b64encode(raw.encode()).decode()

    response = await client.get(
        "/api/v1/findings", headers=_as(uuid.uuid4()), params={"cursor": cursor}
    )

    assert response.status_code == 422


async def test_list_findings_limit_over_max_returns_422(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/findings", headers=_as(uuid.uuid4()), params={"limit": "101"}
    )

    assert response.status_code == 422


async def test_get_finding_returns_evidence(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    finding_id = await insert_finding(rollback_conn, company_id, employee_id)

    response = await client.get(f"/api/v1/findings/{finding_id}", headers=_as(company_id))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(finding_id)
    assert body["employee_id"] == str(employee_id)
    assert body["occurred_on"] == "2026-01-06"
    assert body["evidence"] == {}


async def test_get_finding_of_other_company_returns_404(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_a = await insert_company(rollback_conn)
    company_b = await insert_company(rollback_conn)
    finding_of_b = await insert_finding(
        rollback_conn, company_b, await insert_employee(rollback_conn, company_b)
    )

    response = await client.get(f"/api/v1/findings/{finding_of_b}", headers=_as(company_a))

    assert response.status_code == 404
