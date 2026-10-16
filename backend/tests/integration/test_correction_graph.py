import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Final

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg import OperationalError
from psycopg.errors import (
    AdminShutdown,
    ConnectionFailure,
    DeadlockDetected,
    DiskFull,
    QueryCanceled,
)
from psycopg_pool import PoolClosed, PoolTimeout
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import SUGGEST_LLM_CALLS
from app.db.models import AttendanceEvent, Shift
from app.errors import ConflictError, ServiceUnavailableError
from app.graph import correction_graph
from app.graph.correction_graph import (
    AWAITING_DECISION,
    FINDING_CHANGED_MESSAGE,
    JUDGE_STORE_RESERVE_SECONDS,
    STORE_UNAVAILABLE_MESSAGE,
    SUGGEST_TIMEOUT_MESSAGE,
    CorrectionContext,
    CorrectionGraph,
    CorrectionState,
    CorrectionStepError,
    Decision,
    Suggestion,
    build_correction_graph,
    decide_correction,
    suggest_correction,
)
from app.llm.propose import INVALID_FIX_REASON, NO_ROWS_REASON
from app.llm.provider import (
    ChatMessage,
    LLMProvider,
    LLMResponse,
    LLMUnavailableError,
    ResponseSchema,
)
from app.services.correction_decisions import (
    STALE_ROW_MESSAGE,
    SuggestOutcome,
    Verdict,
    get_correction,
    lock_pending_correction,
)
from app.services.correction_guard import (
    ALREADY_GONE_REASON,
    BREAKS_RULE_REASON,
    INVALID_PATCH_REASON,
    MOVES_PROBLEM_REASON,
    NOT_RESOLVED_REASON,
    ROW_BUSY_REASON,
    UNCHECKED_REASON,
    ResolveResult,
)
from app.services.corrections import PENDING_CORRECTION_MESSAGE
from app.services.users import Approver
from tests.factories import (
    FIXED_SHIFT_END,
    ShiftFixCase,
    SuggestCase,
    fix_clock_out_already,
    insert_employee,
    insert_pending_correction,
    insert_rival_shift_fix,
    insert_shift_fix_case,
    insert_suggest_case,
    remove_overtime_policy,
    set_finding_facts,
    shift_end_fix,
)
from tests.fake_llm import TEST_SUGGEST_TIMEOUT_SECONDS, FakeLLM


@pytest.fixture
def graph() -> CorrectionGraph:
    return build_correction_graph(InMemorySaver())


def _decision(case: ShiftFixCase, verdict: Verdict) -> Decision:
    return Decision(verdict=verdict, approver=Approver(user_id=case.admin_id, role="admin"))


def _thread(case: ShiftFixCase) -> RunnableConfig:
    return {"configurable": {"thread_id": str(case.correction_id)}}


async def _interrupt_count(graph: CorrectionGraph, case: ShiftFixCase) -> int:
    return len([s async for s in graph.aget_state_history(_thread(case)) if s.interrupts])


async def _set_finding_status(conn: AsyncConnection, case: ShiftFixCase, status: str) -> None:
    await conn.execute(
        text("UPDATE findings SET status = :s WHERE id = :id"), {"s": status, "id": case.finding_id}
    )


async def test_decide_opens_review_for_proposal_without_thread(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    view = await decide_correction(
        graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "approve")
    )

    ends_at = await rollback_conn.scalar(select(Shift.ends_at).where(Shift.id == case.shift_id))
    assert (view.status, view.decided_by_user_id) == ("approved", case.admin_id)
    assert ends_at == FIXED_SHIFT_END
    assert await _interrupt_count(graph, case) == 1


async def test_decide_resumes_existing_interrupt(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await graph.ainvoke(
        {"entry": "review"},
        _thread(case),
        context=CorrectionContext(conn=rollback_conn, tenant=case.tenant),
        durability="sync",
    )

    view = await decide_correction(
        graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "reject")
    )

    assert view.status == "rejected"
    assert await _interrupt_count(graph, case) == 1


async def test_reject_after_failed_approve_uses_new_verdict(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    await _set_finding_status(rollback_conn, case, "dismissed")
    with pytest.raises(ConflictError, match="dismissed"):
        await decide_correction(
            graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "approve")
        )
    await _set_finding_status(rollback_conn, case, "open")

    view = await decide_correction(
        graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "reject")
    )

    ends_at = await rollback_conn.scalar(select(Shift.ends_at).where(Shift.id == case.shift_id))
    assert view.status == "rejected"
    assert ends_at != FIXED_SHIFT_END


async def test_stale_approve_keeps_proposal_pending_and_rejectable(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    rival_id = await insert_rival_shift_fix(rollback_conn, case, "2026-01-05T14:00:00+00:00")
    await decide_correction(
        graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "approve")
    )

    with pytest.raises(ConflictError) as caught:
        await decide_correction(
            graph, rollback_conn, case.tenant, rival_id, _decision(case, "approve")
        )
    pending = await get_correction(rollback_conn, case.tenant, rival_id)
    rejected = await decide_correction(
        graph, rollback_conn, case.tenant, rival_id, _decision(case, "reject")
    )

    ends_at = await rollback_conn.scalar(select(Shift.ends_at).where(Shift.id == case.shift_id))
    assert str(caught.value) == STALE_ROW_MESSAGE
    assert (pending.status, rejected.status) == ("pending", "rejected")
    assert ends_at == FIXED_SHIFT_END


async def test_resume_without_decision_is_conflict(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    context = CorrectionContext(conn=rollback_conn, tenant=case.tenant)
    await graph.ainvoke({"entry": "review"}, _thread(case), context=context, durability="sync")

    with pytest.raises(ConflictError, match="No decision"):
        await graph.ainvoke(Command(resume=True), _thread(case), context=context, durability="sync")


async def test_correction_state_has_no_row_ids(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_shift_fix_case(rollback_conn)

    await decide_correction(
        graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "approve")
    )

    snapshots = [s async for s in graph.aget_state_history(_thread(case))]
    stored = json.dumps(
        [[s.values, [i.value for i in s.interrupts]] for s in snapshots], default=str
    )
    ids = (case.correction_id, case.finding_id, case.shift_id, case.employee_id, case.admin_id)
    assert {key for s in snapshots for key in s.values} <= {"entry", "outcome", "decision"}
    assert [i.value for s in snapshots for i in s.interrupts] == [AWAITING_DECISION]
    assert [str(row_id) for row_id in ids if str(row_id) in stored] == []


async def _ask_once(state: CorrectionState) -> CorrectionState:
    interrupt(AWAITING_DECISION)
    return {}


async def _ask_twice(state: CorrectionState) -> CorrectionState:
    interrupt(AWAITING_DECISION)
    interrupt(AWAITING_DECISION)
    return {}


def _graph_with_single_node(node_name: str) -> CorrectionGraph:
    nodes = {"ask_once": _ask_once, "ask_twice": _ask_twice}
    builder = StateGraph(CorrectionState, context_schema=CorrectionContext)
    builder.add_node(node_name, nodes[node_name])
    builder.add_edge(START, node_name)
    builder.add_edge(node_name, END)
    return builder.compile(checkpointer=InMemorySaver())


@pytest.mark.parametrize("node_name", ["ask_once", "ask_twice"])
async def test_decide_that_records_no_decision_is_conflict(
    rollback_conn: AsyncConnection, node_name: str
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    graph = _graph_with_single_node(node_name)

    with pytest.raises(ConflictError, match="was not recorded") as caught:
        await decide_correction(
            graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "approve")
        )

    assert str(case.correction_id) not in str(caught.value)


@pytest.mark.parametrize(("routed", "verdict"), [("approve", "reject"), ("reject", "approve")])
async def test_decision_node_refuses_other_verdict(
    rollback_conn: AsyncConnection, graph: CorrectionGraph, routed: Verdict, verdict: Verdict
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    locked = await lock_pending_correction(rollback_conn, case.tenant, case.correction_id)
    context = CorrectionContext(
        conn=rollback_conn, tenant=case.tenant, decision=_decision(case, verdict), locked=locked
    )
    await graph.ainvoke({"entry": "review"}, _thread(case), context=context, durability="sync")
    await graph.aupdate_state(_thread(case), {"decision": routed}, as_node="await_decision")

    with pytest.raises(ConflictError, match="does not match"):
        await graph.ainvoke(None, _thread(case), context=context, durability="sync")

    status = await rollback_conn.scalar(
        text("SELECT status FROM proposed_corrections WHERE id = :id"), {"id": case.correction_id}
    )
    ends_at = await rollback_conn.scalar(select(Shift.ends_at).where(Shift.id == case.shift_id))
    assert (status, ends_at == FIXED_SHIFT_END) == ("pending", False)


STALL_TEST_TIMEOUT_SECONDS: Final = 0.2
TEST_GUARD_SECONDS: Final = 5


class StalledSaver(InMemorySaver):
    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        await asyncio.Event().wait()
        async for item in super().alist(config, filter=filter, before=before, limit=limit):
            yield item


class FailingSaver(InMemorySaver):
    def __init__(self, error: OperationalError) -> None:
        super().__init__()
        self.error = error

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        raise self.error
        yield


async def _pending_status(conn: AsyncConnection, case: ShiftFixCase) -> str:
    result = await conn.execute(
        text("SELECT status FROM proposed_corrections WHERE id = :id"), {"id": case.correction_id}
    )
    status: str = result.scalar_one()
    return status


async def test_decide_with_stalled_checkpointer_is_service_unavailable(
    rollback_conn: AsyncConnection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(correction_graph, "DECIDE_TIMEOUT_SECONDS", STALL_TEST_TIMEOUT_SECONDS)
    case = await insert_shift_fix_case(rollback_conn)
    graph = build_correction_graph(StalledSaver())
    decide = decide_correction(
        graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "approve")
    )

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceUnavailableError) as caught:
        await asyncio.wait_for(decide, TEST_GUARD_SECONDS)

    assert str(case.correction_id) not in str(caught.value)
    assert [r.getMessage() for r in caplog.records if "TimeoutError" in r.getMessage()] != []
    assert await _pending_status(rollback_conn, case) == "pending"


@pytest.mark.parametrize(
    "error",
    [
        PoolTimeout("pool timeout"),
        PoolClosed("pool closed"),
        OperationalError("consuming input failed"),
        ConnectionFailure("connection failure"),
        AdminShutdown("terminating connection"),
        QueryCanceled("canceling statement due to statement timeout"),
    ],
)
async def test_decide_with_checkpointer_connection_failure_is_service_unavailable(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture, error: OperationalError
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    graph = build_correction_graph(FailingSaver(error))

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceUnavailableError):
        await decide_correction(
            graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "approve")
        )

    messages = [r.getMessage() for r in caplog.records]
    assert [m for m in messages if type(error).__name__ in m] != []
    assert [m for m in messages if str(case.correction_id) in m or str(error) in m] == []


@pytest.mark.parametrize("error", [DeadlockDetected("deadlock"), DiskFull("disk full")])
async def test_decide_with_checkpointer_query_error_is_not_service_unavailable(
    rollback_conn: AsyncConnection, error: OperationalError
) -> None:
    case = await insert_shift_fix_case(rollback_conn)
    graph = build_correction_graph(FailingSaver(error))

    with pytest.raises(type(error)):
        await decide_correction(
            graph, rollback_conn, case.tenant, case.correction_id, _decision(case, "approve")
        )


SUGGEST_FIX_REPLY: Final = json.dumps(
    {
        "action": "fix",
        "target": "row_3",
        "column": "occurred_at",
        "value": "2026-01-06T20:00:00+00:00",
        "reason": "The clock-out is one hour late.",
    }
)
SUGGEST_SHIFT_FIX_REPLY: Final = json.dumps(
    {
        "action": "fix",
        "target": "row_1",
        "column": "ends_at",
        "value": "2026-01-06T20:00:00+00:00",
        "reason": "The shift ends one hour late.",
    }
)
JUDGE_REASON: Final = "The clock-out matches the last door exit."
JUDGE_SUPPORTED_REPLY: Final = json.dumps({"supported": True, "reason": JUDGE_REASON})
SUGGEST_NO_FIX_REPLY: Final = json.dumps(
    {"action": "no_fix", "target": "", "column": "", "value": "", "reason": "Real overwork."}
)
SUGGEST_UNKNOWN_ALIAS_REPLY: Final = SUGGEST_FIX_REPLY.replace("row_3", "row_9")
SUGGEST_TWENTY_SECOND_FIX_REPLY: Final = SUGGEST_FIX_REPLY.replace("T20:00:00", "T20:59:40")
SUGGEST_ELEVEN_HOUR_FIX_REPLY: Final = SUGGEST_FIX_REPLY.replace("T20:00:00", "T19:00:00")
SUGGEST_CLOCK_IN_MOVE_REPLY: Final = json.dumps(
    {
        "action": "fix",
        "target": "row_2",
        "column": "occurred_at",
        "value": "2026-01-05T23:59:00+00:00",
        "reason": "The clock-in belongs to the day before.",
    }
)
SUGGEST_CLOCK_IN_BEFORE_POLICY_REPLY: Final = SUGGEST_CLOCK_IN_MOVE_REPLY.replace(
    "2026-01-05T23:59:00", "2024-06-03T08:00:00"
)
SUGGEST_SHIFT_START_MOVE_REPLY: Final = json.dumps(
    {
        "action": "fix",
        "target": "row_1",
        "column": "starts_at",
        "value": "2026-01-05T08:00:00+00:00",
        "reason": "The shift started a day earlier.",
    }
)

DataChange = Callable[[AsyncConnection, SuggestCase], Awaitable[None]]
SLOW_SUGGEST_TIMEOUT_SECONDS: Final = 0.2


class FailingPutSaver(InMemorySaver):
    def __init__(self, error: OperationalError) -> None:
        super().__init__()
        self.error = error

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        raise self.error


class StalledLLM:
    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        await asyncio.Event().wait()
        raise AssertionError("the stalled model never answers")


class ReassigningLLM(FakeLLM):
    def __init__(self, conn: AsyncConnection, case: SuggestCase, other_employee_id: uuid.UUID):
        super().__init__(SUGGEST_SHIFT_FIX_REPLY)
        self.conn = conn
        self.case = case
        self.other_employee_id = other_employee_id

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        await self.conn.execute(
            text("UPDATE shifts SET employee_id = :e WHERE id = :id"),
            {"e": self.other_employee_id, "id": self.case.shift_id},
        )
        return await super().complete(messages, schema)


class DismissingLLM(FakeLLM):
    def __init__(self, conn: AsyncConnection, case: SuggestCase):
        super().__init__(SUGGEST_FIX_REPLY)
        self.conn = conn
        self.case = case

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        await self.conn.execute(
            text("UPDATE findings SET status = 'dismissed' WHERE id = :id"),
            {"id": self.case.finding_id},
        )
        return await super().complete(messages, schema)


class ProposingLLM(FakeLLM):
    def __init__(self, conn: AsyncConnection, case: SuggestCase):
        super().__init__(SUGGEST_FIX_REPLY, JUDGE_SUPPORTED_REPLY)
        self.conn = conn
        self.case = case

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        if not self.calls:
            await _insert_pending(self.conn, self.case)
        return await super().complete(messages, schema)


async def _insert_pending(conn: AsyncConnection, case: SuggestCase) -> uuid.UUID:
    proposal = shift_end_fix(case.finding_id, case.shift_id, "2026-01-06T19:00:00+00:00")
    return await insert_pending_correction(conn, case.company_id, proposal)


class UndeletableSaver(InMemorySaver):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def adelete_thread(self, thread_id: str) -> None:
        raise self.error


def _suggest_ids(case: SuggestCase) -> tuple[uuid.UUID, ...]:
    return (case.finding_id, case.shift_id, case.clock_out_id, case.employee_id, case.company_id)


def _saver_storage(saver: InMemorySaver) -> str:
    return repr(dict(saver.storage)) + repr(dict(saver.writes)) + repr(dict(saver.blobs))


def _ids_in(stored: str, case: SuggestCase) -> list[str]:
    return [str(row_id) for row_id in _suggest_ids(case) if str(row_id) in stored]


async def _proposal_count(conn: AsyncConnection, case: SuggestCase) -> int:
    count = await conn.scalar(
        text("SELECT count(*) FROM proposed_corrections WHERE finding_id = :id"),
        {"id": case.finding_id},
    )
    return count or 0


async def _suggest(
    graph: CorrectionGraph, conn: AsyncConnection, case: SuggestCase, provider: LLMProvider
) -> SuggestOutcome:
    return await suggest_correction(
        graph,
        conn,
        case.tenant,
        provider,
        case.finding_id,
        timeout_seconds=TEST_SUGGEST_TIMEOUT_SECONDS,
    )


async def _invoke_suggest(
    graph: CorrectionGraph, conn: AsyncConnection, case: SuggestCase, provider: LLMProvider
) -> None:
    suggestion = Suggestion(
        correction_id=uuid.uuid4(), finding_id=case.finding_id, provider=provider
    )
    context = CorrectionContext(conn=conn, tenant=case.tenant, suggestion=suggestion)
    thread: RunnableConfig = {"configurable": {"thread_id": str(suggestion.correction_id)}}
    await graph.ainvoke({"entry": "suggest"}, thread, context=context, durability="sync")


async def test_suggest_state_has_no_row_ids(rollback_conn: AsyncConnection) -> None:
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()
    graph = build_correction_graph(saver)
    fake = FakeLLM(SUGGEST_FIX_REPLY, JUDGE_SUPPORTED_REPLY)

    outcome = await _suggest(graph, rollback_conn, case, fake)

    assert outcome.proposal is not None
    thread: RunnableConfig = {"configurable": {"thread_id": str(outcome.proposal.id)}}
    snapshot = await graph.aget_state(thread)
    stored = json.dumps([snapshot.values, [i.value for i in snapshot.interrupts]], default=str)
    assert snapshot.values == {"entry": "suggest", "outcome": "proposed"}
    assert [i.value for i in snapshot.interrupts] == [AWAITING_DECISION]
    assert _ids_in(stored + _saver_storage(saver), case) == []
    assert JUDGE_REASON not in _saver_storage(saver)
    assert len(fake.calls) == 2


async def test_suggest_with_proposal_makes_exactly_the_budgeted_llm_calls(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_suggest_case(rollback_conn)
    fake = FakeLLM(SUGGEST_FIX_REPLY, JUDGE_SUPPORTED_REPLY)

    outcome = await _suggest(graph, rollback_conn, case, fake)

    assert outcome.proposal is not None
    assert len(fake.calls) == SUGGEST_LLM_CALLS


async def test_suggest_stores_judge_verdict(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_suggest_case(rollback_conn)
    fake = FakeLLM(SUGGEST_FIX_REPLY, JUDGE_SUPPORTED_REPLY)

    outcome = await _suggest(graph, rollback_conn, case, fake)

    assert outcome.proposal is not None
    proposal = outcome.proposal
    assert (proposal.judge_supported, proposal.judge_reason, proposal.judged_by) == (
        True,
        JUDGE_REASON,
        "judge_proposal.v1",
    )
    assert [schema.name for schema in fake.schemas] == ["_ProposalReply", "ProposalVerdict"]


async def test_suggest_judge_outage_stores_proposal_with_null_verdict(
    rollback_conn: AsyncConnection, graph: CorrectionGraph, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    down = LLMUnavailableError("down")
    fake = FakeLLM(SUGGEST_FIX_REPLY, down, down)

    with caplog.at_level(logging.WARNING):
        outcome = await _suggest(graph, rollback_conn, case, fake)

    messages = [record.getMessage() for record in caplog.records]
    assert outcome.proposal is not None
    proposal = outcome.proposal
    assert (proposal.judge_supported, proposal.judge_reason, proposal.judged_by) == (
        None,
        None,
        "judge_proposal.v1",
    )
    assert await _proposal_count(rollback_conn, case) == 1
    assert [m for m in messages if "proposal judge unavailable: llm_unavailable" in m] != []
    assert [m for m in messages if _ids_in(m, case)] == []


async def test_suggest_without_fix_does_not_ask_the_judge(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_suggest_case(rollback_conn)
    fake = FakeLLM(SUGGEST_TWENTY_SECOND_FIX_REPLY, JUDGE_SUPPORTED_REPLY)

    outcome = await _suggest(graph, rollback_conn, case, fake)

    assert (outcome.proposal, outcome.reason) == (None, NOT_RESOLVED_REASON)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("reply", [SUGGEST_NO_FIX_REPLY, SUGGEST_UNKNOWN_ALIAS_REPLY])
async def test_suggest_without_proposal_deletes_its_thread(
    rollback_conn: AsyncConnection, reply: str
) -> None:
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()

    outcome = await _suggest(build_correction_graph(saver), rollback_conn, case, FakeLLM(reply))

    assert outcome.proposal is None
    assert _saver_storage(saver) == "{}{}{}"


async def test_suggest_no_fix_returns_model_reason(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_suggest_case(rollback_conn)

    outcome = await _suggest(graph, rollback_conn, case, FakeLLM(SUGGEST_NO_FIX_REPLY))

    assert (outcome.proposal, outcome.reason) == (None, "Real overwork.")


async def test_suggest_invalid_output_logs_fallback_reason(
    rollback_conn: AsyncConnection, graph: CorrectionGraph, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)

    with caplog.at_level(logging.WARNING):
        outcome = await _suggest(graph, rollback_conn, case, FakeLLM(SUGGEST_UNKNOWN_ALIAS_REPLY))

    messages = [record.getMessage() for record in caplog.records]
    assert outcome.reason == INVALID_FIX_REASON
    assert [m for m in messages if "invalid_output" in m] != []
    assert [m for m in messages if "row_9" in m or str(case.finding_id) in m] == []


@pytest.mark.parametrize("day", [date(1970, 1, 1), date(2100, 12, 31)])
async def test_suggest_edge_day_finding_is_no_fix(
    rollback_conn: AsyncConnection, graph: CorrectionGraph, day: date
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await set_finding_facts(
        rollback_conn, case.finding_id, rule_id="overtime_breach", occurred_on=day
    )
    fake = FakeLLM()

    outcome = await _suggest(graph, rollback_conn, case, fake)

    assert (outcome.proposal, outcome.reason) == (None, NO_ROWS_REASON)
    assert fake.calls == []


async def test_suggest_finding_changed_in_node_leaves_no_ids_in_checkpoints(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await rollback_conn.execute(
        text("UPDATE findings SET status = 'dismissed' WHERE id = :id"), {"id": case.finding_id}
    )
    saver = InMemorySaver()

    with pytest.raises(ConflictError) as caught:
        await _invoke_suggest(build_correction_graph(saver), rollback_conn, case, FakeLLM())

    stored = _saver_storage(saver)
    assert str(caught.value) == FINDING_CHANGED_MESSAGE
    assert "ConflictError" in stored
    assert _ids_in(stored, case) == []


async def test_suggest_target_of_other_employee_is_step_error_without_ids(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    other_employee_id = await insert_employee(rollback_conn, case.company_id)
    provider = ReassigningLLM(rollback_conn, case, other_employee_id)
    saver = InMemorySaver()

    with pytest.raises(CorrectionStepError):
        await _invoke_suggest(build_correction_graph(saver), rollback_conn, case, provider)

    stored = _saver_storage(saver)
    assert "CorrectionStepError" in stored
    assert _ids_in(stored, case) == []
    assert str(other_employee_id) not in stored


async def test_suggest_with_checkpointer_connection_failure_is_service_unavailable(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    graph = build_correction_graph(FailingPutSaver(ConnectionFailure("connection failure")))

    with pytest.raises(ServiceUnavailableError) as caught:
        await _suggest(graph, rollback_conn, case, FakeLLM(SUGGEST_FIX_REPLY))

    assert str(caught.value) == STORE_UNAVAILABLE_MESSAGE


async def test_suggest_with_stalled_model_is_service_unavailable(
    rollback_conn: AsyncConnection, graph: CorrectionGraph, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    suggest = suggest_correction(
        graph,
        rollback_conn,
        case.tenant,
        StalledLLM(),
        case.finding_id,
        timeout_seconds=SLOW_SUGGEST_TIMEOUT_SECONDS,
    )

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceUnavailableError) as caught:
        await asyncio.wait_for(suggest, TEST_GUARD_SECONDS)

    assert str(caught.value) == SUGGEST_TIMEOUT_MESSAGE
    assert [r.getMessage() for r in caplog.records if "TimeoutError" in r.getMessage()] != []


async def test_suggest_llm_outage_deletes_its_thread(rollback_conn: AsyncConnection) -> None:
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()
    fake = FakeLLM(LLMUnavailableError("down"), LLMUnavailableError("down"))

    with pytest.raises(LLMUnavailableError):
        await _suggest(build_correction_graph(saver), rollback_conn, case, fake)

    assert _saver_storage(saver) == "{}{}{}"


async def test_suggest_timeout_deletes_its_thread(rollback_conn: AsyncConnection) -> None:
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()
    suggest = suggest_correction(
        build_correction_graph(saver),
        rollback_conn,
        case.tenant,
        StalledLLM(),
        case.finding_id,
        timeout_seconds=SLOW_SUGGEST_TIMEOUT_SECONDS,
    )

    with pytest.raises(ServiceUnavailableError, match="did not finish in time"):
        await asyncio.wait_for(suggest, TEST_GUARD_SECONDS)

    assert _saver_storage(saver) == "{}{}{}"


async def test_suggest_finding_changed_deletes_its_thread(rollback_conn: AsyncConnection) -> None:
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()
    provider = DismissingLLM(rollback_conn, case)

    with pytest.raises(ConflictError, match="finding changed"):
        await _suggest(build_correction_graph(saver), rollback_conn, case, provider)

    assert _saver_storage(saver) == "{}{}{}"


async def test_suggest_step_error_deletes_its_thread(rollback_conn: AsyncConnection) -> None:
    case = await insert_suggest_case(rollback_conn)
    other_employee_id = await insert_employee(rollback_conn, case.company_id)
    saver = InMemorySaver()
    provider = ReassigningLLM(rollback_conn, case, other_employee_id)

    with pytest.raises(CorrectionStepError):
        await _suggest(build_correction_graph(saver), rollback_conn, case, provider)

    assert _saver_storage(saver) == "{}{}{}"


@pytest.mark.parametrize(
    "error",
    [ConnectionFailure("connection failure"), DeadlockDetected("deadlock"), RuntimeError("boom")],
)
async def test_suggest_no_fix_survives_failed_thread_cleanup(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    case = await insert_suggest_case(rollback_conn)
    graph = build_correction_graph(UndeletableSaver(error))

    with caplog.at_level(logging.WARNING):
        outcome = await _suggest(graph, rollback_conn, case, FakeLLM(SUGGEST_NO_FIX_REPLY))

    messages = [record.getMessage() for record in caplog.records]
    assert (outcome.proposal, outcome.reason) == (None, "Real overwork.")
    assert [m for m in messages if type(error).__name__ in m] != []
    assert [m for m in messages if str(error) in m] == []


async def test_suggest_failure_keeps_original_error_when_cleanup_fails(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    graph = build_correction_graph(UndeletableSaver(ConnectionFailure("connection failure")))
    fake = FakeLLM(LLMUnavailableError("down"), LLMUnavailableError("down"))

    with pytest.raises(LLMUnavailableError, match="The AI model is unavailable"):
        await _suggest(graph, rollback_conn, case, fake)


async def test_suggest_with_pending_proposal_skips_the_model(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await _insert_pending(rollback_conn, case)
    saver = InMemorySaver()
    fake = FakeLLM(SUGGEST_SHIFT_FIX_REPLY)

    with pytest.raises(ConflictError) as caught:
        await _suggest(build_correction_graph(saver), rollback_conn, case, fake)

    assert str(caught.value) == PENDING_CORRECTION_MESSAGE
    assert fake.calls == []
    assert _saver_storage(saver) == "{}{}{}"


async def test_suggest_pending_proposal_made_meanwhile_is_conflict_and_deletes_thread(
    rollback_conn: AsyncConnection,
) -> None:
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()
    provider = ProposingLLM(rollback_conn, case)

    with pytest.raises(ConflictError) as caught:
        await _suggest(build_correction_graph(saver), rollback_conn, case, provider)

    assert str(caught.value) == PENDING_CORRECTION_MESSAGE
    assert [schema.name for schema in provider.schemas] == ["_ProposalReply", "ProposalVerdict"]
    assert _saver_storage(saver) == "{}{}{}"


async def test_suggest_shift_end_fix_is_not_resolved(rollback_conn: AsyncConnection) -> None:
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()
    provider = FakeLLM(SUGGEST_SHIFT_FIX_REPLY)

    outcome = await _suggest(build_correction_graph(saver), rollback_conn, case, provider)

    assert (outcome.proposal, outcome.reason) == (
        None,
        "The suggested change would not resolve this finding.",
    )
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"


async def _suggest_logged(
    conn: AsyncConnection,
    case: SuggestCase,
    provider: LLMProvider,
    caplog: pytest.LogCaptureFixture,
) -> tuple[SuggestOutcome, InMemorySaver, list[str]]:
    saver = InMemorySaver()
    with caplog.at_level(logging.WARNING):
        outcome = await _suggest(build_correction_graph(saver), conn, case, provider)
    return outcome, saver, [record.getMessage() for record in caplog.records]


async def _event_time(conn: AsyncConnection, event_id: uuid.UUID) -> datetime | None:
    return await conn.scalar(
        select(AttendanceEvent.occurred_at).where(AttendanceEvent.id == event_id)
    )


class ChangingLLM(FakeLLM):
    def __init__(self, conn: AsyncConnection, case: SuggestCase, change: DataChange):
        super().__init__(SUGGEST_ELEVEN_HOUR_FIX_REPLY)
        self.conn = conn
        self.case = case
        self.change = change

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        await self.change(self.conn, self.case)
        return await super().complete(messages, schema)


@pytest.mark.parametrize(
    ("reply", "reason", "logged"),
    [
        (SUGGEST_TWENTY_SECOND_FIX_REPLY, NOT_RESOLVED_REASON, "not_resolved"),
        (SUGGEST_SHIFT_START_MOVE_REPLY, MOVES_PROBLEM_REASON, "moves_problem"),
        (SUGGEST_CLOCK_IN_MOVE_REPLY, MOVES_PROBLEM_REASON, "moves_problem"),
    ],
)
async def test_suggest_fix_that_does_not_clear_finding_is_no_fix(
    rollback_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
    reply: str,
    reason: str,
    logged: str,
) -> None:
    case = await insert_suggest_case(rollback_conn)

    outcome, saver, messages = await _suggest_logged(rollback_conn, case, FakeLLM(reply), caplog)

    assert (outcome.proposal, outcome.reason) == (None, reason)
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"
    assert [m for m in messages if logged in m] != []
    assert [m for m in messages if _ids_in(m, case)] == []


async def test_suggest_on_already_fixed_finding_is_no_fix_without_asking_the_model(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await fix_clock_out_already(rollback_conn, case)
    await rollback_conn.execute(
        text("UPDATE findings SET status = 'confirmed' WHERE id = :id"), {"id": case.finding_id}
    )
    fake = FakeLLM(SUGGEST_ELEVEN_HOUR_FIX_REPLY)

    outcome, saver, messages = await _suggest_logged(rollback_conn, case, fake, caplog)

    assert (outcome.proposal, outcome.reason) == (None, ALREADY_GONE_REASON)
    assert fake.calls == []
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"
    assert [m for m in messages if "already_gone" in m] != []
    assert [m for m in messages if _ids_in(m, case)] == []


async def test_suggest_without_overtime_policy_is_no_fix_without_asking_the_model(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    await remove_overtime_policy(rollback_conn, case)
    fake = FakeLLM(SUGGEST_FIX_REPLY)

    outcome, saver, messages = await _suggest_logged(rollback_conn, case, fake, caplog)

    assert (outcome.proposal, outcome.reason) == (None, UNCHECKED_REASON)
    assert fake.calls == []
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"
    assert [m for m in messages if "MissingOvertimePolicyError" in m] != []
    assert [m for m in messages if "unchecked" in m] != []
    assert [m for m in messages if _ids_in(m, case) or "2026-01-06" in m] == []


@pytest.mark.parametrize(
    ("change", "reason"),
    [(fix_clock_out_already, ALREADY_GONE_REASON), (remove_overtime_policy, UNCHECKED_REASON)],
)
async def test_suggest_rechecks_the_rule_after_data_changed_during_the_model_call(
    rollback_conn: AsyncConnection, graph: CorrectionGraph, change: DataChange, reason: str
) -> None:
    case = await insert_suggest_case(rollback_conn)
    provider = ChangingLLM(rollback_conn, case, change)

    outcome = await _suggest(graph, rollback_conn, case, provider)

    assert (outcome.proposal, outcome.reason) == (None, reason)
    assert len(provider.calls) == 1
    assert await _proposal_count(rollback_conn, case) == 0


async def test_suggest_fix_that_stops_the_rule_from_running_leaves_clock_in_unchanged(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    fake = FakeLLM(SUGGEST_CLOCK_IN_BEFORE_POLICY_REPLY)

    outcome, saver, messages = await _suggest_logged(rollback_conn, case, fake, caplog)

    assert (outcome.proposal, outcome.reason) == (None, BREAKS_RULE_REASON)
    assert await _event_time(rollback_conn, case.clock_in_id) == datetime(2026, 1, 6, 8, tzinfo=UTC)
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"
    assert [m for m in messages if "patched data" in m and "MissingOvertimePolicyError" in m]
    assert [m for m in messages if "breaks_rule" in m] != []
    assert [m for m in messages if _ids_in(m, case) or "2024-06-03" in m] == []


async def test_suggest_fix_that_breaks_a_constraint_is_invalid_patch_no_fix(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    fake = FakeLLM(SUGGEST_SHIFT_FIX_REPLY.replace("T20:00:00", "T07:00:00"))

    outcome, saver, messages = await _suggest_logged(rollback_conn, case, fake, caplog)

    assert (outcome.proposal, outcome.reason) == (None, INVALID_PATCH_REASON)
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"
    assert [m for m in messages if "invalid_patch" in m] != []
    assert [m for m in messages if _ids_in(m, case)] == []


@pytest.mark.parametrize(
    "expected",
    [
        ("still_detected", NOT_RESOLVED_REASON, "not_resolved"),
        ("moves_problem", MOVES_PROBLEM_REASON, "moves_problem"),
        ("already_gone", ALREADY_GONE_REASON, "already_gone"),
        ("unchecked", UNCHECKED_REASON, "unchecked"),
        ("invalid_patch", INVALID_PATCH_REASON, "invalid_patch"),
        ("breaks_rule", BREAKS_RULE_REASON, "breaks_rule"),
        ("busy", ROW_BUSY_REASON, "busy"),
        ("outcome_added_later", UNCHECKED_REASON, "outcome_added_later"),
    ],
)
async def test_suggest_maps_every_guard_outcome_to_a_safe_no_fix(
    rollback_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    expected: tuple[str, str, str],
) -> None:
    check, reason, logged = expected

    async def _fixed_outcome(*args: object) -> ResolveResult:
        return ResolveResult(check=check, before={})  # type: ignore[arg-type]

    monkeypatch.setattr(correction_graph, "check_fix_resolves", _fixed_outcome)
    case = await insert_suggest_case(rollback_conn)

    outcome, saver, messages = await _suggest_logged(
        rollback_conn, case, FakeLLM(SUGGEST_FIX_REPLY), caplog
    )

    assert (outcome.proposal, outcome.reason) == (None, reason)
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"
    assert [m for m in messages if logged in m] != []


async def test_suggest_unmapped_guard_outcome_is_logged_as_error(
    rollback_conn: AsyncConnection,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _unmapped_outcome(*args: object) -> ResolveResult:
        return ResolveResult(check="outcome_added_later", before={})  # type: ignore[arg-type]

    monkeypatch.setattr(correction_graph, "check_fix_resolves", _unmapped_outcome)
    case = await insert_suggest_case(rollback_conn)

    await _suggest_logged(rollback_conn, case, FakeLLM(SUGGEST_FIX_REPLY), caplog)

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert [m for m in errors if "outcome_added_later" in m] != []


async def test_suggest_resolving_fix_stores_proposal_and_leaves_clock_out_unchanged(
    rollback_conn: AsyncConnection, graph: CorrectionGraph
) -> None:
    case = await insert_suggest_case(rollback_conn)

    outcome = await _suggest(graph, rollback_conn, case, FakeLLM(SUGGEST_FIX_REPLY))

    clock_out = await _event_time(rollback_conn, case.clock_out_id)
    assert outcome.proposal is not None
    assert (outcome.proposal.target_id, outcome.proposal.patch) == (
        case.clock_out_id,
        {"occurred_at": "2026-01-06T20:00:00Z"},
    )
    assert clock_out == datetime(2026, 1, 6, 21, tzinfo=UTC)


class RowChangingJudgeLLM(FakeLLM):
    def __init__(self, conn: AsyncConnection, case: SuggestCase):
        super().__init__(SUGGEST_FIX_REPLY, JUDGE_SUPPORTED_REPLY)
        self.conn = conn
        self.case = case

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        if self.calls:
            await self.conn.execute(
                text("UPDATE attendance_events SET occurred_at = :t WHERE id = :id"),
                {"t": datetime(2026, 1, 6, 20, 30, tzinfo=UTC), "id": self.case.clock_out_id},
            )
        return await super().complete(messages, schema)


class StallingJudgeLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__(SUGGEST_FIX_REPLY)

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        if self.calls:
            self.calls.append(list(messages))
            await asyncio.Event().wait()
        return await super().complete(messages, schema)


async def test_suggest_target_row_changed_during_judge_is_conflict_and_stores_nothing(
    rollback_conn: AsyncConnection, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()
    provider = RowChangingJudgeLLM(rollback_conn, case)

    with caplog.at_level(logging.WARNING), pytest.raises(ConflictError) as caught:
        await _suggest(build_correction_graph(saver), rollback_conn, case, provider)

    assert str(caught.value) == FINDING_CHANGED_MESSAGE
    assert len(provider.calls) == 2
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"
    assert [r.getMessage() for r in caplog.records if _ids_in(r.getMessage(), case)] == []


async def test_suggest_judge_past_its_deadline_stores_proposal_with_null_verdict(
    rollback_conn: AsyncConnection, graph: CorrectionGraph, caplog: pytest.LogCaptureFixture
) -> None:
    case = await insert_suggest_case(rollback_conn)
    provider = StallingJudgeLLM()
    suggest = suggest_correction(
        graph,
        rollback_conn,
        case.tenant,
        provider,
        case.finding_id,
        timeout_seconds=JUDGE_STORE_RESERVE_SECONDS + 1,
    )

    with caplog.at_level(logging.WARNING):
        outcome = await asyncio.wait_for(suggest, JUDGE_STORE_RESERVE_SECONDS + TEST_GUARD_SECONDS)

    assert outcome.proposal is not None
    proposal = outcome.proposal
    assert (proposal.judge_supported, proposal.judge_reason, proposal.judged_by) == (
        None,
        None,
        "judge_proposal.v1",
    )
    assert len(provider.calls) == 2
    assert [r.getMessage() for r in caplog.records if "proposal judge timed out" in r.getMessage()]


async def test_suggest_budget_expiring_during_judge_is_service_unavailable(
    rollback_conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(correction_graph, "JUDGE_STORE_RESERVE_SECONDS", -TEST_GUARD_SECONDS)
    case = await insert_suggest_case(rollback_conn)
    saver = InMemorySaver()
    suggest = suggest_correction(
        build_correction_graph(saver),
        rollback_conn,
        case.tenant,
        StallingJudgeLLM(),
        case.finding_id,
        timeout_seconds=SLOW_SUGGEST_TIMEOUT_SECONDS,
    )

    with pytest.raises(ServiceUnavailableError) as caught:
        await asyncio.wait_for(suggest, TEST_GUARD_SECONDS)

    assert str(caught.value) == SUGGEST_TIMEOUT_MESSAGE
    assert await _proposal_count(rollback_conn, case) == 0
    assert _saver_storage(saver) == "{}{}{}"
