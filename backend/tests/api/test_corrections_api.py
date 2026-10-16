import json
import uuid
from typing import Final

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AuditLog, ProposedCorrection
from app.llm.propose import INVALID_FIX_REASON
from app.llm.provider import LLMUnavailableError
from app.services.correction_decisions import STALE_ROW_MESSAGE
from app.services.corrections import PENDING_CORRECTION_MESSAGE
from tests.factories import (
    ShiftFixCase,
    SuggestCase,
    insert_company,
    insert_pending_correction,
    insert_rival_shift_fix,
    insert_shift_fix_case,
    insert_suggest_case,
    insert_user,
    shift_end_fix,
)

SUGGESTED_REASON: Final = "The clock-out is one hour late."
FIX_REPLY: Final = json.dumps(
    {
        "action": "fix",
        "target": "row_3",
        "column": "occurred_at",
        "value": "2026-01-06T20:00:00+00:00",
        "reason": SUGGESTED_REASON,
    }
)
TWENTY_SECOND_FIX_REPLY: Final = FIX_REPLY.replace("T20:00:00", "T20:59:40")
NO_FIX_REPLY: Final = json.dumps(
    {"action": "no_fix", "target": "", "column": "", "value": "", "reason": "Real overwork."}
)
STALE_RECOVERY_DETAIL: Final = (
    "The row changed since this fix was proposed. Reject it and ask for a new suggestion."
)
UNKNOWN_ALIAS_REPLY: Final = FIX_REPLY.replace("row_3", "row_9")
OUTAGE: Final = (LLMUnavailableError("down"), LLMUnavailableError("down"))
JUDGE_REASON: Final = "The clock-out matches the last door exit."
JUDGE_REPLY: Final = json.dumps({"supported": False, "reason": JUDGE_REASON})


def _headers(company_id: uuid.UUID, user_id: uuid.UUID | str | None = None) -> dict[str, str]:
    headers = {"X-Company-Id": str(company_id)}
    if user_id is not None:
        headers["X-User-Id"] = str(user_id)
    return headers


async def _decide(
    client: AsyncClient,
    correction_id: uuid.UUID,
    verdict: str,
    headers: dict[str, str],
) -> Response:
    return await client.post(f"/api/v1/corrections/{correction_id}/{verdict}", headers=headers)


async def _status(conn: AsyncConnection, case: ShiftFixCase) -> str:
    result = await conn.execute(
        select(ProposedCorrection.status).where(ProposedCorrection.id == case.correction_id)
    )
    return result.scalar_one()


async def test_list_corrections_returns_pending_proposal(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    response = await client.get(
        f"/api/v1/findings/{case.finding_id}/corrections", headers=_headers(case.company_id)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [(item["id"], item["status"]) for item in body] == [(str(case.correction_id), "pending")]
    assert body[0]["patch"] == {"ends_at": "2026-01-05T15:00:00Z"}
    assert body[0]["current"] == {"ends_at": "2026-01-05T16:00:00Z"}
    assert (body[0]["decided_by_user_id"], body[0]["decided_at"]) == (None, None)


async def test_list_corrections_returns_before_and_judge_fields(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    response = await client.get(
        f"/api/v1/findings/{case.finding_id}/corrections", headers=_headers(case.company_id)
    )

    assert response.status_code == 200, response.text
    item = response.json()[0]
    assert item["before"] == {"ends_at": "2026-01-05T16:00:00Z"}
    assert (item["judge_supported"], item["judge_reason"], item["judged_by"]) == (None, None, None)


async def test_list_corrections_legacy_proposal_returns_null_before_and_judge(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await rollback_conn.execute(
        text("UPDATE proposed_corrections SET before = NULL WHERE id = :id"),
        {"id": case.correction_id},
    )

    response = await client.get(
        f"/api/v1/findings/{case.finding_id}/corrections", headers=_headers(case.company_id)
    )

    assert response.status_code == 200, response.text
    item = response.json()[0]
    assert (item["before"], item["judge_supported"], item["judged_by"]) == (None, None, None)
    assert item["current"] == {"ends_at": "2026-01-05T16:00:00Z"}


async def test_approve_after_row_changed_since_proposal_is_conflict(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    rival_id = await insert_rival_shift_fix(rollback_conn, case, "2026-01-05T14:00:00+00:00")
    headers = _headers(case.company_id, case.admin_id)
    first = await _decide(client, case.correction_id, "approve", headers)

    response = await _decide(client, rival_id, "approve", headers)

    ends_at = await rollback_conn.scalar(
        text("SELECT ends_at FROM shifts WHERE id = :id"), {"id": case.shift_id}
    )
    assert first.status_code == 200, first.text
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == STALE_ROW_MESSAGE
    assert ends_at.isoformat() == "2026-01-05T15:00:00+00:00"


async def test_approve_returns_approved_view(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    response = await _decide(
        client, case.correction_id, "approve", _headers(case.company_id, case.admin_id)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["decided_by_user_id"]) == ("approved", str(case.admin_id))
    assert body["current"] == {"ends_at": "2026-01-05T15:00:00Z"}


async def test_reject_returns_rejected_view(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    manager_id = await insert_user(rollback_conn, case.company_id, "manager")

    response = await _decide(
        client, case.correction_id, "reject", _headers(case.company_id, manager_id)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["decided_by_user_id"]) == ("rejected", str(manager_id))
    assert body["current"] == {"ends_at": "2026-01-05T16:00:00Z"}


async def test_approve_without_user_header_is_unauthenticated(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    response = await _decide(client, case.correction_id, "approve", _headers(case.company_id))

    assert response.status_code == 401
    assert await _status(rollback_conn, case) == "pending"


async def test_approve_malformed_user_header_is_unprocessable(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    response = await _decide(
        client, case.correction_id, "approve", _headers(case.company_id, "not-a-uuid")
    )

    assert response.status_code == 422


async def test_approve_auditor_is_forbidden(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    auditor_id = await insert_user(rollback_conn, case.company_id, "auditor")

    response = await _decide(
        client, case.correction_id, "approve", _headers(case.company_id, auditor_id)
    )

    assert response.status_code == 403
    assert await _status(rollback_conn, case) == "pending"


async def test_approve_other_company_user_is_forbidden(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    other = await insert_shift_fix_case(rollback_conn)

    response = await _decide(
        client, case.correction_id, "approve", _headers(case.company_id, other.admin_id)
    )

    assert response.status_code == 403
    assert str(other.admin_id) not in response.text


async def test_approve_unknown_proposal_by_auditor_is_forbidden(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)
    auditor_id = await insert_user(rollback_conn, company_id, "auditor")

    response = await _decide(client, uuid.uuid4(), "approve", _headers(company_id, auditor_id))

    assert response.status_code == 403


async def test_approve_other_company_proposal_is_not_found(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    other = await insert_shift_fix_case(rollback_conn)

    response = await _decide(
        client, case.correction_id, "approve", _headers(other.company_id, other.admin_id)
    )

    assert response.status_code == 404
    assert await _status(rollback_conn, case) == "pending"


async def test_double_approve_is_conflict(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    headers = _headers(case.company_id, case.admin_id)
    await _decide(client, case.correction_id, "approve", headers)

    response = await _decide(client, case.correction_id, "approve", headers)

    assert response.status_code == 409
    assert "already approved" in response.json()["detail"]


async def test_decide_unknown_company_is_not_found(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    response = await _decide(client, case.correction_id, "reject", _headers(uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown company."


async def test_list_corrections_unknown_finding_is_not_found(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    company_id = await insert_company(rollback_conn)

    response = await client.get(
        f"/api/v1/findings/{uuid.uuid4()}/corrections", headers=_headers(company_id)
    )

    assert response.status_code == 404


async def _suggest(
    client: AsyncClient, case: SuggestCase, finding_id: uuid.UUID | None = None
) -> Response:
    return await client.post(
        f"/api/v1/findings/{finding_id or case.finding_id}/corrections",
        headers=_headers(case.company_id),
    )


async def _stored_counts(conn: AsyncConnection, case: SuggestCase) -> tuple[int, int]:
    proposals = await conn.scalar(
        select(func.count())
        .select_from(ProposedCorrection)
        .where(ProposedCorrection.finding_id == case.finding_id)
    )
    audits = await conn.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.company_id == case.company_id)
    )
    return proposals or 0, audits or 0


@pytest.mark.parametrize("fake_llm", [(FIX_REPLY, JUDGE_REPLY)], indirect=True)
async def test_suggest_returns_created_pending_proposal(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)

    response = await _suggest(client, case)

    assert response.status_code == 201, response.text
    body = response.json()
    proposal = body["proposal"]
    assert body["reason"] is None
    assert (proposal["status"], proposal["target_table"]) == ("pending", "attendance_events")
    assert proposal["target_id"] == str(case.clock_out_id)
    assert proposal["patch"] == {"occurred_at": "2026-01-06T20:00:00Z"}
    assert proposal["current"] == {"occurred_at": "2026-01-06T21:00:00Z"}
    assert proposal["reason"] == SUGGESTED_REASON
    assert proposal["proposed_by"] == "llm:fake-llm:propose_correction.v1"
    assert (proposal["judge_supported"], proposal["judge_reason"], proposal["judged_by"]) == (
        False,
        JUDGE_REASON,
        "judge_proposal.v1",
    )


@pytest.mark.parametrize("fake_llm", [(FIX_REPLY, *OUTAGE)], indirect=True)
async def test_suggest_judge_outage_stores_proposal_with_null_verdict(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)

    response = await _suggest(client, case)

    assert response.status_code == 201, response.text
    proposal = response.json()["proposal"]
    assert (proposal["judge_supported"], proposal["judge_reason"], proposal["judged_by"]) == (
        None,
        None,
        "judge_proposal.v1",
    )
    assert await _stored_counts(rollback_conn, case) == (1, 1)


@pytest.mark.parametrize("fake_llm", [(NO_FIX_REPLY,)], indirect=True)
async def test_suggest_no_fix_returns_reason(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)

    response = await _suggest(client, case)

    assert response.status_code == 200, response.text
    assert response.json() == {"proposal": None, "reason": "Real overwork."}
    assert await _stored_counts(rollback_conn, case) == (0, 0)


@pytest.mark.parametrize("fake_llm", [(TWENTY_SECOND_FIX_REPLY,)], indirect=True)
async def test_suggest_fix_that_does_not_clear_finding_returns_reason(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)

    response = await _suggest(client, case)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "proposal": None,
        "reason": "The suggested change would not resolve this finding.",
    }
    assert await _stored_counts(rollback_conn, case) == (0, 0)


@pytest.mark.parametrize("fake_llm", [(UNKNOWN_ALIAS_REPLY,)], indirect=True)
async def test_suggest_invalid_output_stores_nothing(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)

    response = await _suggest(client, case)

    assert response.status_code == 200, response.text
    assert response.json() == {"proposal": None, "reason": INVALID_FIX_REASON}
    assert await _stored_counts(rollback_conn, case) == (0, 0)


@pytest.mark.parametrize("fake_llm", [OUTAGE], indirect=True)
async def test_suggest_llm_outage_is_service_unavailable(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)

    response = await _suggest(client, case)

    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "The AI model is unavailable. Try again later."}
    assert await _stored_counts(rollback_conn, case) == (0, 0)


async def test_suggest_unknown_finding_is_not_found(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)

    response = await _suggest(client, case, uuid.uuid4())

    assert response.status_code == 404, response.text


async def test_suggest_dismissed_finding_is_conflict(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await rollback_conn.execute(
        text("UPDATE findings SET status = 'dismissed' WHERE id = :id"), {"id": case.finding_id}
    )

    response = await _suggest(client, case)

    assert response.status_code == 409, response.text
    assert "dismissed" in response.json()["detail"]


@pytest.mark.parametrize("fake_llm", [(FIX_REPLY,)], indirect=True)
async def test_suggest_does_not_require_user_header(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)

    response = await client.post(
        f"/api/v1/findings/{case.finding_id}/corrections",
        headers={"X-Company-Id": str(case.company_id)},
    )

    assert response.status_code == 201, response.text


async def _insert_pending(conn: AsyncConnection, case: SuggestCase) -> uuid.UUID:
    proposal = shift_end_fix(case.finding_id, case.shift_id, "2026-01-06T19:00:00+00:00")
    return await insert_pending_correction(conn, case.company_id, proposal)


@pytest.mark.parametrize("fake_llm", [(FIX_REPLY,)], indirect=True)
async def test_suggest_with_pending_proposal_is_conflict(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await _insert_pending(rollback_conn, case)

    response = await _suggest(client, case)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": PENDING_CORRECTION_MESSAGE}
    assert await _stored_counts(rollback_conn, case) == (1, 1)


@pytest.mark.parametrize("fake_llm", [(FIX_REPLY,)], indirect=True)
@pytest.mark.parametrize("verdict", ["approve", "reject"])
async def test_suggest_after_decided_proposal_creates_new_proposal(
    client: AsyncClient, rollback_conn: AsyncConnection, verdict: str
) -> None:
    case = await insert_suggest_case(rollback_conn)
    correction_id = await _insert_pending(rollback_conn, case)
    decided = await _decide(
        client, correction_id, verdict, _headers(case.company_id, case.admin_id)
    )
    assert decided.status_code == 200, decided.text

    response = await _suggest(client, case)

    assert response.status_code == 201, response.text
    assert response.json()["proposal"]["status"] == "pending"


@pytest.mark.parametrize("fake_llm", [(FIX_REPLY, FIX_REPLY)], indirect=True)
async def test_stale_fix_recovers_by_reject_then_new_suggest(
    client: AsyncClient, rollback_conn: AsyncConnection
) -> None:
    case = await insert_suggest_case(rollback_conn)
    stale_id = await _insert_pending(rollback_conn, case)
    rival_id = await insert_rival_shift_fix(rollback_conn, case, "2026-01-06T19:30:00+00:00")
    headers = _headers(case.company_id, case.admin_id)
    rival = await _decide(client, rival_id, "approve", headers)
    assert rival.status_code == 200, rival.text

    stale = await _decide(client, stale_id, "approve", headers)
    blocked = await _suggest(client, case)
    rejected = await _decide(client, stale_id, "reject", headers)
    renewed = await _suggest(client, case)

    assert (stale.status_code, stale.json()["detail"]) == (409, STALE_RECOVERY_DETAIL)
    assert (blocked.status_code, blocked.json()["detail"]) == (409, PENDING_CORRECTION_MESSAGE)
    assert rejected.status_code == 200, rejected.text
    assert renewed.status_code == 201, renewed.text
    assert renewed.json()["proposal"]["status"] == "pending"
