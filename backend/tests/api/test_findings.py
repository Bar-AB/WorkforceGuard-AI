import base64
import uuid
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import (
    insert_company,
    insert_employee,
    insert_finding,
    mark_finding_explained,
    set_finding_facts,
)

KNOWN_RULE = "overtime_breach"


def _as(company_id: uuid.UUID) -> dict[str, str]:
    return {"X-Company-Id": str(company_id)}


@pytest.fixture
async def known_company(rollback_conn: AsyncConnection) -> uuid.UUID:
    return await insert_company(rollback_conn)


async def _finding_with(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    *,
    rule_id: str = KNOWN_RULE,
    severity: str = "high",
    occurred_on: date = date(2026, 1, 6),
) -> str:
    employee_id = await insert_employee(conn, company_id)
    finding_id = await insert_finding(conn, company_id, employee_id)
    await set_finding_facts(
        conn, finding_id, rule_id=rule_id, severity=severity, occurred_on=occurred_on
    )
    return str(finding_id)


async def _listed_ids(
    client: AsyncClient, company_id: uuid.UUID, params: dict[str, str]
) -> list[str]:
    response = await client.get("/api/v1/findings", headers=_as(company_id), params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


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


async def test_list_findings_bad_cursor_returns_422(
    client: AsyncClient, known_company: uuid.UUID
) -> None:
    response = await client.get(
        "/api/v1/findings", headers=_as(known_company), params={"cursor": "not-a-cursor"}
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
    client: AsyncClient, known_company: uuid.UUID, detected_at: str
) -> None:
    raw = f"{detected_at}|{uuid.uuid4()}"
    cursor = base64.urlsafe_b64encode(raw.encode()).decode()

    response = await client.get(
        "/api/v1/findings", headers=_as(known_company), params={"cursor": cursor}
    )

    assert response.status_code == 422


async def test_list_findings_limit_over_max_returns_422(
    client: AsyncClient, known_company: uuid.UUID
) -> None:
    response = await client.get(
        "/api/v1/findings", headers=_as(known_company), params={"limit": "101"}
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


async def test_list_findings_filters_by_rule_id(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    match = await _finding_with(rollback_conn, company_id, rule_id=KNOWN_RULE)
    await _finding_with(rollback_conn, company_id, rule_id="overtime")

    assert await _listed_ids(client, company_id, {"rule_id": KNOWN_RULE}) == [match]


async def test_list_findings_filters_by_severity(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    match = await _finding_with(rollback_conn, company_id, severity="medium")
    await _finding_with(rollback_conn, company_id, severity="high")
    await _finding_with(rollback_conn, company_id, severity="low")

    assert await _listed_ids(client, company_id, {"severity": "medium"}) == [match]


async def test_list_findings_occurred_range_is_inclusive(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    on_from = await _finding_with(rollback_conn, company_id, occurred_on=date(2026, 2, 1))
    on_to = await _finding_with(rollback_conn, company_id, occurred_on=date(2026, 2, 10))
    await _finding_with(rollback_conn, company_id, occurred_on=date(2026, 1, 31))
    await _finding_with(rollback_conn, company_id, occurred_on=date(2026, 2, 11))

    listed = await _listed_ids(
        client, company_id, {"occurred_from": "2026-02-01", "occurred_to": "2026-02-10"}
    )

    assert sorted(listed) == sorted([on_from, on_to])


async def test_list_findings_combined_filters(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    march = date(2026, 3, 5)
    match = await _finding_with(rollback_conn, company_id, severity="low", occurred_on=march)
    await _finding_with(rollback_conn, company_id, severity="high", occurred_on=march)
    await _finding_with(rollback_conn, company_id, severity="low", occurred_on=date(2026, 4, 5))
    await _finding_with(
        rollback_conn, company_id, rule_id="overtime", severity="low", occurred_on=march
    )

    listed = await _listed_ids(
        client,
        company_id,
        {
            "rule_id": KNOWN_RULE,
            "severity": "low",
            "occurred_from": "2026-03-01",
            "occurred_to": "2026-03-31",
        },
    )

    assert listed == [match]


async def test_list_findings_filters_keep_tenant_isolation(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_a = await insert_company(rollback_conn)
    company_b = await insert_company(rollback_conn)
    own = await _finding_with(rollback_conn, company_a, severity="low")
    await _finding_with(rollback_conn, company_b, severity="low")

    listed = await _listed_ids(client, company_a, {"severity": "low", "rule_id": KNOWN_RULE})

    assert listed == [own]


async def test_list_findings_filtered_paging_visits_each_match_once(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    matches = [await _finding_with(rollback_conn, company_id, severity="low") for _ in range(5)]
    for _ in range(3):
        await _finding_with(rollback_conn, company_id, severity="high")

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"limit": "2", "severity": "low", **({"cursor": cursor} if cursor else {})}
        response = await client.get("/api/v1/findings", headers=_as(company_id), params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen += [item["id"] for item in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert sorted(seen) == sorted(matches)


async def test_list_findings_unknown_rule_id_returns_422(
    client: AsyncClient, known_company: uuid.UUID
) -> None:
    response = await client.get(
        "/api/v1/findings", headers=_as(known_company), params={"rule_id": "no_such_rule"}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Unknown rule_id."}


async def test_list_findings_bad_severity_returns_422(
    client: AsyncClient, known_company: uuid.UUID
) -> None:
    response = await client.get(
        "/api/v1/findings", headers=_as(known_company), params={"severity": "critical"}
    )

    assert response.status_code == 422


async def test_list_findings_inverted_occurred_range_returns_422(
    client: AsyncClient, known_company: uuid.UUID
) -> None:
    response = await client.get(
        "/api/v1/findings",
        headers=_as(known_company),
        params={"occurred_from": "2026-02-10", "occurred_to": "2026-02-01"},
    )

    assert response.status_code == 422
    assert "occurred_from" in response.json()["detail"]


async def test_get_finding_returns_explanation_fields(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    finding_id = await insert_finding(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id)
    )
    await mark_finding_explained(rollback_conn, finding_id, explanation="Worked 13 hours.")

    response = await client.get(f"/api/v1/findings/{finding_id}", headers=_as(company_id))

    assert response.status_code == 200
    body = response.json()
    assert body["explanation"] == "Worked 13 hours."
    assert body["explanation_source"] == "llm"
    assert body["explanation_prompt_version"] == "explain_finding.v1"


async def test_get_finding_unexplained_has_null_explanation_fields(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    finding_id = await insert_finding(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id)
    )

    response = await client.get(f"/api/v1/findings/{finding_id}", headers=_as(company_id))

    assert response.status_code == 200
    body = response.json()
    assert body["explanation"] is None
    assert body["explanation_source"] is None
    assert body["explanation_prompt_version"] is None


async def test_get_finding_response_has_dashboard_fields(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    finding_id = await insert_finding(
        rollback_conn, company_id, await insert_employee(rollback_conn, company_id)
    )

    response = await client.get(f"/api/v1/findings/{finding_id}", headers=_as(company_id))

    assert set(response.json()) == {
        "id",
        "employee_id",
        "rule_id",
        "rule_version",
        "severity",
        "status",
        "summary",
        "occurred_on",
        "detected_at",
        "evidence",
        "explanation",
        "explanation_source",
        "explanation_prompt_version",
    }


async def test_list_findings_of_unknown_company_returns_404(client: AsyncClient) -> None:
    unknown_company = uuid.uuid4()

    response = await client.get("/api/v1/findings", headers=_as(unknown_company))

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown company."}
