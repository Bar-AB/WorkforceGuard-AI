import asyncio
import logging
import uuid
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from psycopg import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.errors import ConflictError, DomainError, NotFoundError, ServiceUnavailableError
from app.llm.proposal_judge import UNJUDGED, judge_fix
from app.llm.propose import NoFix, ProposedFix, ProposeResult, propose_fix
from app.llm.provider import LLMProvider
from app.security.tenant import TenantContext
from app.services.correction_candidates import CorrectionCandidates, load_correction_candidates
from app.services.correction_decisions import (
    CorrectionView,
    LockedCorrection,
    SuggestOutcome,
    Verdict,
    approve_correction,
    get_correction,
    lock_pending_correction,
    reject_correction,
)
from app.services.correction_guard import (
    ALREADY_GONE_REASON,
    BREAKS_RULE_REASON,
    INVALID_PATCH_REASON,
    MOVES_PROBLEM_REASON,
    NOT_RESOLVED_REASON,
    ROW_BUSY_REASON,
    UNCHECKED_REASON,
    DetectionCheck,
    ResolveCheck,
    check_finding_detected,
    check_fix_resolves,
)
from app.services.corrections import (
    CorrectionProposal,
    FixJudgement,
    PendingCorrectionError,
    propose_correction,
    require_correctable_finding,
    require_no_pending_correction,
)
from app.services.users import Approver

AWAITING_DECISION: Final = {"awaiting": "decision"}
NO_DECISION_MESSAGE: Final = "No decision was given for this correction."
VERDICT_MISMATCH_MESSAGE: Final = "The decision does not match this step."
STEP_FAILED_MESSAGE: Final = "A correction step failed."
NOT_RECORDED_MESSAGE: Final = "The decision was not recorded. Try again."
STORE_UNAVAILABLE_MESSAGE: Final = "The correction store is unavailable. Try again shortly."
DECIDE_TIMEOUT_MESSAGE: Final = "The decision did not finish in time. Try again shortly."
SUGGEST_TIMEOUT_MESSAGE: Final = "The fix suggestion did not finish in time. Try again later."
FINDING_CHANGED_MESSAGE: Final = "The finding changed while a fix was being suggested. Try again."
NO_SUGGESTION_MESSAGE: Final = "No finding was given for this suggestion."
_OUTAGE_SQLSTATE_PREFIXES: Final = ("08", "57P", "57014")
_INTERRUPTED: Final = "__interrupt__"
DECIDE_TIMEOUT_SECONDS: Final = 10.0
JUDGE_STORE_RESERVE_SECONDS: Final = 5.0
_UNCHECKED: Final = NoFix(reason=UNCHECKED_REASON, fallback_reason="unchecked")
_GUARD_NO_FIX: Final[Mapping[ResolveCheck | DetectionCheck, NoFix]] = {
    "still_detected": NoFix(reason=NOT_RESOLVED_REASON, fallback_reason="not_resolved"),
    "moves_problem": NoFix(reason=MOVES_PROBLEM_REASON, fallback_reason="moves_problem"),
    "unchecked": _UNCHECKED,
    "breaks_rule": NoFix(reason=BREAKS_RULE_REASON, fallback_reason="breaks_rule"),
    "already_gone": NoFix(reason=ALREADY_GONE_REASON, fallback_reason="already_gone"),
    "invalid_patch": NoFix(reason=INVALID_PATCH_REASON, fallback_reason="invalid_patch"),
    "busy": NoFix(reason=ROW_BUSY_REASON, fallback_reason="busy"),
}

Entry = Literal["suggest", "review"]
Outcome = Literal["detected", "proposed", "no_fix"]

logger = logging.getLogger(__name__)


class CorrectionStepError(RuntimeError):
    pass


class CorrectionStoreUnavailableError(ServiceUnavailableError):
    pass


class CorrectionState(TypedDict, total=False):
    entry: Entry
    outcome: Outcome
    decision: Verdict


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    approver: Approver


@dataclass
class SuggestScratch:
    candidates: CorrectionCandidates | None = None
    result: ProposeResult | None = None
    checked_before: dict[str, object] | None = None
    judgement: FixJudgement | None = None


@dataclass(frozen=True)
class Suggestion:
    correction_id: uuid.UUID
    finding_id: uuid.UUID
    provider: LLMProvider
    deadline: float | None = None
    scratch: SuggestScratch = field(default_factory=SuggestScratch)


@dataclass(frozen=True)
class CorrectionContext:
    conn: AsyncConnection
    tenant: TenantContext
    decision: Decision | None = None
    locked: LockedCorrection | None = None
    suggestion: Suggestion | None = None


type CorrectionGraph = CompiledStateGraph[
    CorrectionState, CorrectionContext, CorrectionState, CorrectionState
]


def _route_entry(state: CorrectionState) -> Entry:
    return state["entry"]


def _route_outcome(state: CorrectionState) -> Outcome:
    return state["outcome"]


def _route_decision(state: CorrectionState) -> Verdict:
    return state["decision"]


def _require_decision(context: CorrectionContext) -> tuple[Decision, LockedCorrection]:
    if context.decision is None or context.locked is None:
        raise ConflictError(NO_DECISION_MESSAGE)
    return context.decision, context.locked


def _require_verdict(
    context: CorrectionContext, verdict: Verdict
) -> tuple[Decision, LockedCorrection]:
    decision, locked = _require_decision(context)
    if decision.verdict != verdict:
        raise ConflictError(VERDICT_MISMATCH_MESSAGE)
    return decision, locked


def _require_suggestion(context: CorrectionContext) -> Suggestion:
    if context.suggestion is None:
        raise ConflictError(NO_SUGGESTION_MESSAGE)
    return context.suggestion


def _require_candidates(suggestion: Suggestion) -> CorrectionCandidates:
    if suggestion.scratch.candidates is None:
        raise CorrectionStepError(STEP_FAILED_MESSAGE)
    return suggestion.scratch.candidates


def _require_checked_before(suggestion: Suggestion) -> dict[str, object]:
    if suggestion.scratch.checked_before is None:
        raise CorrectionStepError(STEP_FAILED_MESSAGE)
    return suggestion.scratch.checked_before


def _require_fix(suggestion: Suggestion) -> ProposedFix:
    if not isinstance(suggestion.scratch.result, ProposedFix):
        raise CorrectionStepError(STEP_FAILED_MESSAGE)
    return suggestion.scratch.result


async def _run_step[T](step: Awaitable[T]) -> T:
    try:
        return await step
    except DomainError:
        raise
    except Exception as error:
        raise CorrectionStepError(STEP_FAILED_MESSAGE) from error


async def _run_finding_step[T](step: Awaitable[T]) -> T:
    try:
        return await _run_step(step)
    except PendingCorrectionError:
        raise
    except (NotFoundError, ConflictError) as error:
        raise ConflictError(FINDING_CHANGED_MESSAGE) from error
    except DomainError as error:
        raise CorrectionStepError(STEP_FAILED_MESSAGE) from error


async def _load_facts(
    state: CorrectionState, runtime: Runtime[CorrectionContext]
) -> CorrectionState:
    context = runtime.context
    suggestion = _require_suggestion(context)
    suggestion.scratch.candidates = await _run_finding_step(
        load_correction_candidates(context.conn, context.tenant, suggestion.finding_id)
    )
    return {}


def _no_fix(suggestion: Suggestion, check: ResolveCheck | DetectionCheck) -> CorrectionState:
    no_fix = _GUARD_NO_FIX.get(check)
    if no_fix is None:
        logger.error("correction guard outcome has no no-fix mapping: %s", check)
        no_fix = _UNCHECKED
    suggestion.scratch.result = no_fix
    return {"outcome": "no_fix"}


async def _check_detected(
    state: CorrectionState, runtime: Runtime[CorrectionContext]
) -> CorrectionState:
    context = runtime.context
    suggestion = _require_suggestion(context)
    check = await _run_finding_step(
        check_finding_detected(context.conn, context.tenant, suggestion.finding_id)
    )
    if check == "detected":
        return {"outcome": "detected"}
    return _no_fix(suggestion, check)


def _trace_metadata(context: CorrectionContext) -> dict[str, str]:
    return {"company_id": str(context.tenant.company_id)}


async def _propose(state: CorrectionState, runtime: Runtime[CorrectionContext]) -> CorrectionState:
    context = runtime.context
    suggestion = _require_suggestion(context)
    candidates = _require_candidates(suggestion)
    result = await _run_step(
        propose_fix(
            suggestion.provider, candidates.facts, candidates.rows, _trace_metadata(context)
        )
    )
    suggestion.scratch.result = result
    return {"outcome": "proposed" if isinstance(result, ProposedFix) else "no_fix"}


def _proposal(suggestion: Suggestion) -> CorrectionProposal:
    fix = _require_fix(suggestion)
    return CorrectionProposal(
        finding_id=suggestion.finding_id,
        target_table=fix.target_table,
        target_id=_require_candidates(suggestion).targets[fix.alias],
        patch=fix.patch,
        reason=fix.reason,
    )


async def _check_resolves(
    state: CorrectionState, runtime: Runtime[CorrectionContext]
) -> CorrectionState:
    context = runtime.context
    suggestion = _require_suggestion(context)
    result = await _run_finding_step(
        check_fix_resolves(context.conn, context.tenant, _proposal(suggestion))
    )
    if result.check == "resolves":
        suggestion.scratch.checked_before = result.before
        return {}
    return _no_fix(suggestion, result.check)


async def _judge(state: CorrectionState, runtime: Runtime[CorrectionContext]) -> CorrectionState:
    context = runtime.context
    suggestion = _require_suggestion(context)
    candidates = _require_candidates(suggestion)
    judging = judge_fix(
        suggestion.provider,
        candidates.facts,
        candidates.rows,
        _require_fix(suggestion),
        _trace_metadata(context),
    )
    suggestion.scratch.judgement = await _run_step(_bounded_judge(judging, suggestion.deadline))
    return {}


def _judge_deadline(suggest_deadline: float | None) -> float | None:
    if suggest_deadline is None:
        return None
    return suggest_deadline - JUDGE_STORE_RESERVE_SECONDS


async def _bounded_judge(
    judging: Awaitable[FixJudgement], suggest_deadline: float | None
) -> FixJudgement:
    try:
        async with asyncio.timeout_at(_judge_deadline(suggest_deadline)):
            return await judging
    except TimeoutError:
        logger.warning("proposal judge timed out")
        return UNJUDGED


async def _store_proposal(
    state: CorrectionState, runtime: Runtime[CorrectionContext]
) -> CorrectionState:
    context = runtime.context
    suggestion = _require_suggestion(context)
    await _run_finding_step(
        propose_correction(
            context.conn,
            context.tenant,
            _proposal(suggestion),
            _require_fix(suggestion).proposed_by,
            correction_id=suggestion.correction_id,
            judgement=suggestion.scratch.judgement,
            checked_before=_require_checked_before(suggestion),
        )
    )
    return {}


async def _await_decision(
    state: CorrectionState, runtime: Runtime[CorrectionContext]
) -> CorrectionState:
    interrupt(AWAITING_DECISION)
    decision, _ = _require_decision(runtime.context)
    return {"decision": decision.verdict}


async def _apply(state: CorrectionState, runtime: Runtime[CorrectionContext]) -> CorrectionState:
    context = runtime.context
    decision, locked = _require_verdict(context, "approve")
    await _run_step(approve_correction(context.conn, context.tenant, locked, decision.approver))
    return {}


async def _reject(state: CorrectionState, runtime: Runtime[CorrectionContext]) -> CorrectionState:
    context = runtime.context
    decision, locked = _require_verdict(context, "reject")
    await _run_step(reject_correction(context.conn, context.tenant, locked, decision.approver))
    return {}


def build_correction_graph(checkpointer: BaseCheckpointSaver[str]) -> CorrectionGraph:
    graph = StateGraph(CorrectionState, context_schema=CorrectionContext)
    graph.add_node("load_facts", _load_facts)
    graph.add_node("check_detected", _check_detected)
    graph.add_node("propose", _propose)
    graph.add_node("check_resolves", _check_resolves)
    graph.add_node("judge", _judge)
    graph.add_node("store_proposal", _store_proposal)
    graph.add_node("await_decision", _await_decision)
    graph.add_node("apply", _apply)
    graph.add_node("reject", _reject)
    graph.add_conditional_edges(
        START, _route_entry, {"suggest": "load_facts", "review": "await_decision"}
    )
    graph.add_edge("load_facts", "check_detected")
    graph.add_conditional_edges(
        "check_detected", _route_outcome, {"detected": "propose", "no_fix": END}
    )
    graph.add_conditional_edges(
        "propose", _route_outcome, {"proposed": "check_resolves", "no_fix": END}
    )
    graph.add_conditional_edges(
        "check_resolves", _route_outcome, {"proposed": "judge", "no_fix": END}
    )
    graph.add_edge("judge", "store_proposal")
    graph.add_edge("store_proposal", "await_decision")
    graph.add_conditional_edges(
        "await_decision", _route_decision, {"approve": "apply", "reject": "reject"}
    )
    graph.add_edge("apply", END)
    graph.add_edge("reject", END)
    return graph.compile(checkpointer=checkpointer)


def _thread_config(correction_id: uuid.UUID) -> RunnableConfig:
    return {"configurable": {"thread_id": str(correction_id)}}


async def _interrupt_config(
    graph: CorrectionGraph, config: RunnableConfig, context: CorrectionContext
) -> RunnableConfig:
    async for snapshot in graph.aget_state_history(config):
        if snapshot.interrupts:
            return snapshot.config
    await graph.ainvoke({"entry": "review"}, config, context=context, durability="sync")
    return (await graph.aget_state(config)).config


async def _resume(
    graph: CorrectionGraph, config: RunnableConfig, context: CorrectionContext
) -> dict[str, Any]:
    resume_from = await _interrupt_config(graph, config, context)
    result: dict[str, Any] = await graph.ainvoke(
        Command(resume=True), resume_from, context=context, durability="sync"
    )
    return result


def _is_store_outage(error: OperationalError) -> bool:
    sqlstate = error.sqlstate
    return sqlstate is None or sqlstate.startswith(_OUTAGE_SQLSTATE_PREFIXES)


def _unavailable(error: Exception, message: str) -> ServiceUnavailableError:
    logger.warning("correction graph unavailable: %s", type(error).__name__)
    return ServiceUnavailableError(message)


def _store_unavailable(error: Exception) -> CorrectionStoreUnavailableError:
    logger.warning("correction graph unavailable: %s", type(error).__name__)
    return CorrectionStoreUnavailableError(STORE_UNAVAILABLE_MESSAGE)


async def _store_guarded[T](run: Awaitable[T]) -> T:
    try:
        return await run
    except OperationalError as error:
        if not _is_store_outage(error):
            raise
        raise _store_unavailable(error) from error


async def _bounded_resume(
    graph: CorrectionGraph, config: RunnableConfig, context: CorrectionContext
) -> dict[str, Any]:
    try:
        async with asyncio.timeout(DECIDE_TIMEOUT_SECONDS):
            return await _store_guarded(_resume(graph, config, context))
    except TimeoutError as error:
        raise _unavailable(error, DECIDE_TIMEOUT_MESSAGE) from error


async def decide_correction(
    graph: CorrectionGraph,
    conn: AsyncConnection,
    tenant: TenantContext,
    correction_id: uuid.UUID,
    decision: Decision,
) -> CorrectionView:
    locked = await lock_pending_correction(conn, tenant, correction_id)
    context = CorrectionContext(conn=conn, tenant=tenant, decision=decision, locked=locked)
    result = await _bounded_resume(graph, _thread_config(correction_id), context)
    view = await get_correction(conn, tenant, correction_id)
    if _INTERRUPTED in result or view.status == "pending":
        raise ConflictError(NOT_RECORDED_MESSAGE)
    return view


async def _bounded_suggest(
    graph: CorrectionGraph,
    context: CorrectionContext,
    correction_id: uuid.UUID,
    timeout_seconds: float,
) -> None:
    config = _thread_config(correction_id)
    try:
        async with asyncio.timeout(timeout_seconds):
            await _store_guarded(
                graph.ainvoke({"entry": "suggest"}, config, context=context, durability="sync")
            )
    except TimeoutError as error:
        await _discard_thread(graph, correction_id)
        raise _unavailable(error, SUGGEST_TIMEOUT_MESSAGE) from error
    except CorrectionStoreUnavailableError:
        raise
    except (DomainError, CorrectionStepError):
        await _discard_thread(graph, correction_id)
        raise


async def _discard_thread(graph: CorrectionGraph, correction_id: uuid.UUID) -> None:
    if not isinstance(graph.checkpointer, BaseCheckpointSaver):
        return
    try:
        await graph.checkpointer.adelete_thread(str(correction_id))
    except Exception as error:
        logger.warning("correction thread cleanup failed: %s", type(error).__name__)


def _log_fallback(result: NoFix) -> None:
    if result.fallback_reason is not None:
        logger.warning("correction suggestion fell back: %s", result.fallback_reason)


async def suggest_correction(  # noqa: PLR0913 - the time limit is keyword-only configuration
    graph: CorrectionGraph,
    conn: AsyncConnection,
    tenant: TenantContext,
    provider: LLMProvider,
    finding_id: uuid.UUID,
    *,
    timeout_seconds: float,
) -> SuggestOutcome:
    await require_correctable_finding(conn, tenant, finding_id)
    await require_no_pending_correction(conn, tenant, finding_id)
    suggestion = Suggestion(
        correction_id=uuid.uuid4(),
        finding_id=finding_id,
        provider=provider,
        deadline=asyncio.get_running_loop().time() + timeout_seconds,
    )
    context = CorrectionContext(conn=conn, tenant=tenant, suggestion=suggestion)
    await _bounded_suggest(graph, context, suggestion.correction_id, timeout_seconds)
    result = suggestion.scratch.result
    if isinstance(result, NoFix):
        _log_fallback(result)
        await _discard_thread(graph, suggestion.correction_id)
        return SuggestOutcome(proposal=None, reason=result.reason)
    view = await get_correction(conn, tenant, suggestion.correction_id)
    return SuggestOutcome(proposal=view, reason=None)
