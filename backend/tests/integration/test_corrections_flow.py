import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Final

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from langchain_core.runnables import RunnableConfig
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.api.deps import get_llm_provider
from app.db.models import AttendanceEvent, AuditLog, Finding, ProposedCorrection, Shift
from app.errors import ConflictError
from app.graph import checkpointer as checkpointer_module
from app.graph.checkpointer import open_checkpointer
from app.graph.correction_graph import (
    CorrectionContext,
    CorrectionGraph,
    CorrectionStepError,
    Decision,
    build_correction_graph,
    decide_correction,
)
from app.llm.provider import LLMProvider
from app.main import create_app
from app.services.users import Approver
from tests.db_helpers import wait_until_lock_wait
from tests.factories import (
    FIXED_SHIFT_END,
    SUGGEST_SHIFT_END,
    ShiftFixCase,
    SuggestCase,
    insert_shift_fix_case,
    insert_suggest_case,
)
from tests.fake_llm import FakeLLM

TEST_STATEMENT_TIMEOUT_MS = 300
LOCK_RELEASE_SECONDS = 12
STALLED_WRITE_BOUND_SECONDS = 5
SUGGESTED_CLOCK_OUT: Final = "2026-01-06T20:00:00+00:00"
SUGGESTED_REASON: Final = "The clock-out is one hour after the shift should have ended."
NO_FIX: Final = json.dumps(
    {"action": "no_fix", "target": "", "column": "", "value": "", "reason": "Real overwork."}
)
CLOCK_OUT_FIX: Final = json.dumps(
    {
        "action": "fix",
        "target": "row_3",
        "column": "occurred_at",
        "value": SUGGESTED_CLOCK_OUT,
        "reason": SUGGESTED_REASON,
    }
)


@asynccontextmanager
async def _running_api(
    database_url: str, provider: LLMProvider | None = None
) -> AsyncIterator[tuple[AsyncClient, FastAPI]]:
    app = create_app()
    if provider is not None:
        app.dependency_overrides[get_llm_provider] = lambda: provider
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


@pytest.fixture
def database_url(committing_database_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", committing_database_url)
    return committing_database_url


@pytest.fixture
async def case(committing_engine: AsyncEngine) -> ShiftFixCase:
    async with committing_engine.begin() as conn:
        return await insert_shift_fix_case(conn)


async def _decide(client: AsyncClient, case: ShiftFixCase, verdict: str) -> Response:
    return await client.post(
        f"/api/v1/corrections/{case.correction_id}/{verdict}",
        headers={"X-Company-Id": str(case.company_id), "X-User-Id": str(case.admin_id)},
    )


def _thread(case: ShiftFixCase) -> RunnableConfig:
    return {"configurable": {"thread_id": str(case.correction_id)}}


async def _interrupt_count(graph: CorrectionGraph, case: ShiftFixCase) -> int:
    snapshots = [s async for s in graph.aget_state_history(_thread(case))]
    return len([s for s in snapshots if s.interrupts])


async def _applied_count(engine: AsyncEngine, case: ShiftFixCase) -> int:
    async with engine.connect() as conn:
        result = await conn.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.company_id == case.company_id, AuditLog.action == "correction.applied")
        )
        return result.scalar_one()


async def _shift_end(engine: AsyncEngine, case: ShiftFixCase) -> datetime:
    async with engine.connect() as conn:
        result = await conn.execute(select(Shift.ends_at).where(Shift.id == case.shift_id))
        return result.scalar_one()


async def _proposal_status(engine: AsyncEngine, case: ShiftFixCase) -> str:
    async with engine.connect() as conn:
        result = await conn.execute(
            select(ProposedCorrection.status).where(ProposedCorrection.id == case.correction_id)
        )
        return result.scalar_one()


async def _refuse_decided_proposal_at_commit(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE FUNCTION refuse_commit() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'commit refused'; END $$"
            )
        )
        await conn.execute(
            text(
                "CREATE CONSTRAINT TRIGGER refuse_decision_at_commit AFTER UPDATE "
                "ON proposed_corrections DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION refuse_commit()"
            )
        )


async def _allow_commits(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DROP TRIGGER refuse_decision_at_commit ON proposed_corrections"))


async def _lock_finding(conn: AsyncConnection, case: ShiftFixCase) -> None:
    await conn.begin()
    await conn.execute(
        select(Finding.id).where(Finding.id == case.finding_id).with_for_update(key_share=True)
    )


async def test_approve_after_restart_resumes_persisted_thread(
    database_url: str, committing_engine: AsyncEngine, case: ShiftFixCase
) -> None:
    async with _running_api(database_url) as (_, app), committing_engine.connect() as conn:
        context = CorrectionContext(conn=conn, tenant=case.tenant)
        await app.state.correction_graph.ainvoke(
            {"entry": "review"}, _thread(case), context=context, durability="sync"
        )

    async with _running_api(database_url) as (client, app):
        response = await _decide(client, case, "approve")
        interrupts = await _interrupt_count(app.state.correction_graph, case)

    assert response.status_code == 200, response.text
    assert interrupts == 1
    assert await _shift_end(committing_engine, case) == FIXED_SHIFT_END


async def test_decide_retry_after_commit_failure_applies_once(
    database_url: str, committing_engine: AsyncEngine, case: ShiftFixCase
) -> None:
    await _refuse_decided_proposal_at_commit(committing_engine)
    async with _running_api(database_url) as (client, _):
        refused = await _decide(client, case, "approve")
        pending_after_refusal = await _proposal_status(committing_engine, case)
        await _allow_commits(committing_engine)

        retried = await _decide(client, case, "approve")

    assert (refused.status_code, pending_after_refusal) == (500, "pending")
    assert retried.status_code == 200, retried.text
    assert await _applied_count(committing_engine, case) == 1
    assert await _shift_end(committing_engine, case) == FIXED_SHIFT_END


async def test_reject_after_failed_approve_leaves_row_unchanged(
    database_url: str, committing_engine: AsyncEngine, case: ShiftFixCase
) -> None:
    original_end = await _shift_end(committing_engine, case)
    await _refuse_decided_proposal_at_commit(committing_engine)
    async with _running_api(database_url) as (client, _):
        refused = await _decide(client, case, "approve")
        await _allow_commits(committing_engine)

        rejected = await _decide(client, case, "reject")

    assert refused.status_code == 500
    assert rejected.status_code == 200, rejected.text
    assert await _proposal_status(committing_engine, case) == "rejected"
    assert await _shift_end(committing_engine, case) == original_end
    assert await _applied_count(committing_engine, case) == 0


async def test_decide_retry_after_in_node_conflict_applies_once(
    database_url: str, committing_engine: AsyncEngine, case: ShiftFixCase
) -> None:
    async with _running_api(database_url) as (client, _):
        async with committing_engine.connect() as scan:
            await _lock_finding(scan, case)
            busy = await _decide(client, case, "approve")
            await scan.rollback()

        retried = await _decide(client, case, "approve")

    assert busy.status_code == 409
    assert "locked by another update" in busy.json()["detail"]
    assert retried.status_code == 200, retried.text
    assert await _applied_count(committing_engine, case) == 1


async def test_concurrent_approve_applies_once(
    committing_database_url: str, committing_engine: AsyncEngine, case: ShiftFixCase
) -> None:
    decision = Decision(verdict="approve", approver=Approver(user_id=case.admin_id, role="admin"))

    async def second_approve(graph: CorrectionGraph) -> None:
        async with committing_engine.begin() as conn:
            await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            await decide_correction(graph, conn, case.tenant, case.correction_id, decision)

    async with open_checkpointer(committing_database_url) as checkpointer:
        graph = build_correction_graph(checkpointer)
        async with committing_engine.connect() as first:
            await first.begin()
            await decide_correction(graph, first, case.tenant, case.correction_id, decision)
            second = asyncio.create_task(second_approve(graph))
            await wait_until_lock_wait(committing_engine)
            await first.commit()
        with pytest.raises(ConflictError, match="already approved"):
            await second

    assert await _applied_count(committing_engine, case) == 1


async def _thread_storage(engine: AsyncEngine, thread_id: uuid.UUID) -> bytes:
    thread = {"t": str(thread_id)}
    async with engine.connect() as conn:
        writes = await conn.execute(
            text("SELECT blob FROM checkpoint_writes WHERE thread_id = :t"), thread
        )
        blobs = await conn.execute(
            text("SELECT blob FROM checkpoint_blobs WHERE thread_id = :t AND blob IS NOT NULL"),
            thread,
        )
        checkpoints = await conn.execute(
            text("SELECT checkpoint::text || metadata::text FROM checkpoints WHERE thread_id = :t"),
            thread,
        )
        stored = [*writes.scalars(), *blobs.scalars()]
        return b"".join(stored) + "".join(checkpoints.scalars()).encode()


def _case_ids_in(stored: bytes, case: ShiftFixCase) -> list[str]:
    ids = (case.correction_id, case.finding_id, case.shift_id, case.employee_id, case.admin_id)
    return [str(row_id) for row_id in ids if str(row_id).encode() in stored]


async def test_failed_approve_leaves_no_ids_in_checkpoints(
    database_url: str, committing_engine: AsyncEngine, case: ShiftFixCase
) -> None:
    async with committing_engine.begin() as conn:
        await conn.execute(
            text("UPDATE findings SET status = 'dismissed' WHERE id = :id"), {"id": case.finding_id}
        )
    async with _running_api(database_url) as (client, _):
        response = await _decide(client, case, "approve")

    stored = await _thread_storage(committing_engine, case.correction_id)
    assert response.status_code == 409
    assert b"ConflictError" in stored
    assert _case_ids_in(stored, case) == []


async def test_unexpected_step_error_leaves_no_values_in_checkpoints(
    committing_database_url: str, committing_engine: AsyncEngine, case: ShiftFixCase
) -> None:
    decision = Decision(verdict="approve", approver=Approver(user_id=case.admin_id, role="admin"))
    async with (
        open_checkpointer(committing_database_url) as checkpointer,
        committing_engine.connect() as holder,
    ):
        graph = build_correction_graph(checkpointer)
        await holder.begin()
        await holder.execute(select(Shift.id).where(Shift.id == case.shift_id).with_for_update())
        with pytest.raises(CorrectionStepError):
            async with committing_engine.begin() as conn:
                await conn.execute(text("SET LOCAL lock_timeout = '200ms'"))
                await decide_correction(graph, conn, case.tenant, case.correction_id, decision)
        await holder.rollback()

    stored = await _thread_storage(committing_engine, case.correction_id)
    assert b"CorrectionStepError" in stored
    assert _case_ids_in(stored, case) == []
    assert [word for word in (b"UPDATE", b"15:00") if word in stored] == []


async def test_decide_with_checkpointer_down_is_service_unavailable(
    database_url: str, committing_engine: AsyncEngine, case: ShiftFixCase
) -> None:
    async with _running_api(database_url) as (client, app):
        await app.state.correction_graph.checkpointer.conn.close()
        response = await _decide(client, case, "approve")

    assert response.status_code == 503
    assert str(case.correction_id) not in response.text
    assert await _proposal_status(committing_engine, case) == "pending"


async def _rollback_after(conn: AsyncConnection, seconds: float) -> None:
    await asyncio.sleep(seconds)
    await conn.rollback()


async def test_decide_with_stalled_checkpoint_write_is_service_unavailable(
    database_url: str,
    committing_engine: AsyncEngine,
    case: ShiftFixCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        checkpointer_module, "CHECKPOINT_STATEMENT_TIMEOUT_MS", TEST_STATEMENT_TIMEOUT_MS
    )
    async with _running_api(database_url) as (client, _), committing_engine.connect() as holder:
        await holder.begin()
        await holder.execute(text("LOCK TABLE checkpoints IN SHARE MODE"))
        release = asyncio.create_task(_rollback_after(holder, LOCK_RELEASE_SECONDS))
        started = time.monotonic()
        response = await _decide(client, case, "approve")
        elapsed = time.monotonic() - started
        release.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await release
        await holder.rollback()

    assert response.status_code == 503, response.text
    assert elapsed < STALLED_WRITE_BOUND_SECONDS
    assert str(case.correction_id) not in response.text
    assert await _proposal_status(committing_engine, case) == "pending"


@pytest.fixture
async def suggest_case(committing_engine: AsyncEngine) -> SuggestCase:
    async with committing_engine.begin() as conn:
        return await insert_suggest_case(conn)


async def _suggest_fix(client: AsyncClient, case: SuggestCase) -> Response:
    return await client.post(
        f"/api/v1/findings/{case.finding_id}/corrections",
        headers={"X-Company-Id": str(case.company_id)},
    )


async def _decide_suggested(
    client: AsyncClient, case: SuggestCase, correction_id: str, verdict: str
) -> Response:
    return await client.post(
        f"/api/v1/corrections/{correction_id}/{verdict}",
        headers={"X-Company-Id": str(case.company_id), "X-User-Id": str(case.admin_id)},
    )


async def _clock_out(engine: AsyncEngine, case: SuggestCase) -> datetime:
    async with engine.connect() as conn:
        result = await conn.execute(
            select(AttendanceEvent.occurred_at).where(AttendanceEvent.id == case.clock_out_id)
        )
        return result.scalar_one()


async def _finding_status(engine: AsyncEngine, case: SuggestCase) -> str:
    async with engine.connect() as conn:
        result = await conn.execute(select(Finding.status).where(Finding.id == case.finding_id))
        return result.scalar_one()


async def _audit_actions(engine: AsyncEngine, case: SuggestCase) -> list[str]:
    async with engine.connect() as conn:
        result = await conn.execute(
            select(AuditLog.action)
            .where(AuditLog.company_id == case.company_id)
            .order_by(AuditLog.action)
        )
        return list(result.scalars())


async def test_suggest_then_approve_changes_row_and_writes_audit_rows(
    database_url: str, committing_engine: AsyncEngine, suggest_case: SuggestCase
) -> None:
    async with _running_api(database_url, FakeLLM(CLOCK_OUT_FIX)) as (client, _):
        suggested = await _suggest_fix(client, suggest_case)
        correction_id = suggested.json()["proposal"]["id"]
        approved = await _decide_suggested(client, suggest_case, correction_id, "approve")

    assert suggested.status_code == 201, suggested.text
    assert approved.status_code == 200, approved.text
    decided = approved.json()
    assert (decided["status"], decided["decided_by_user_id"]) == (
        "approved",
        str(suggest_case.admin_id),
    )
    assert await _clock_out(committing_engine, suggest_case) == datetime.fromisoformat(
        SUGGESTED_CLOCK_OUT
    )
    assert await _finding_status(committing_engine, suggest_case) == "confirmed"
    assert await _audit_actions(committing_engine, suggest_case) == [
        "correction.applied",
        "correction.approved",
        "correction.proposed",
        "finding.confirmed",
    ]


async def test_suggest_then_reject_changes_nothing(
    database_url: str, committing_engine: AsyncEngine, suggest_case: SuggestCase
) -> None:
    async with _running_api(database_url, FakeLLM(CLOCK_OUT_FIX)) as (client, _):
        suggested = await _suggest_fix(client, suggest_case)
        correction_id = suggested.json()["proposal"]["id"]
        rejected = await _decide_suggested(client, suggest_case, correction_id, "reject")

    assert suggested.status_code == 201, suggested.text
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"
    assert await _clock_out(committing_engine, suggest_case) == SUGGEST_SHIFT_END
    assert await _finding_status(committing_engine, suggest_case) == "open"
    assert await _audit_actions(committing_engine, suggest_case) == [
        "correction.proposed",
        "correction.rejected",
    ]


async def _refuse_proposal_insert_at_commit(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE FUNCTION refuse_commit() RETURNS trigger LANGUAGE plpgsql AS "
                "$$ BEGIN RAISE EXCEPTION 'commit refused'; END $$"
            )
        )
        await conn.execute(
            text(
                "CREATE CONSTRAINT TRIGGER refuse_proposal_at_commit AFTER INSERT "
                "ON proposed_corrections DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                "EXECUTE FUNCTION refuse_commit()"
            )
        )


async def _checkpointed_threads(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT DISTINCT thread_id FROM checkpoints"))
        return list(result.scalars())


async def test_suggest_rollback_leaves_decide_not_found(
    database_url: str, committing_engine: AsyncEngine, suggest_case: SuggestCase
) -> None:
    await _refuse_proposal_insert_at_commit(committing_engine)
    async with _running_api(database_url, FakeLLM(CLOCK_OUT_FIX)) as (client, _):
        refused = await _suggest_fix(client, suggest_case)
        [orphan_thread] = await _checkpointed_threads(committing_engine)
        approved = await _decide_suggested(client, suggest_case, orphan_thread, "approve")

    assert refused.status_code == 500
    assert approved.status_code == 404, approved.text
    assert await _clock_out(committing_engine, suggest_case) == SUGGEST_SHIFT_END


async def test_suggest_then_approve_leaves_no_ids_in_checkpoints(
    database_url: str, committing_engine: AsyncEngine, suggest_case: SuggestCase
) -> None:
    async with _running_api(database_url, FakeLLM(CLOCK_OUT_FIX)) as (client, _):
        suggested = await _suggest_fix(client, suggest_case)
        correction_id = uuid.UUID(suggested.json()["proposal"]["id"])
        await _decide_suggested(client, suggest_case, str(correction_id), "approve")

    stored = await _thread_storage(committing_engine, correction_id)
    ids = (
        correction_id,
        suggest_case.finding_id,
        suggest_case.clock_out_id,
        suggest_case.employee_id,
        suggest_case.admin_id,
    )
    assert stored != b""
    assert [str(row_id) for row_id in ids if str(row_id).encode() in stored] == []
    assert [word for word in (b"20:00", SUGGESTED_REASON.encode()) if word in stored] == []


async def test_suggest_no_fix_leaves_no_checkpoints(
    database_url: str, committing_engine: AsyncEngine, suggest_case: SuggestCase
) -> None:
    async with _running_api(database_url, FakeLLM(NO_FIX)) as (client, _):
        response = await _suggest_fix(client, suggest_case)

    assert response.status_code == 200, response.text
    assert await _checkpointed_threads(committing_engine) == []
