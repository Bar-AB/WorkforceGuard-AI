import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, TypedDict, get_args

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncConnection

from app.llm.explain import explain_finding
from app.llm.provider import LLMProvider
from app.llm.structured import FallbackReason
from app.rules.findings import RuleFinding
from app.security.tenant import TenantContext
from app.services import companies, scans
from app.services.explanations import (
    ExplainedFinding,
    PendingFinding,
    pending_explanations,
    store_explanations,
)
from app.services.scans import DetectionSummary, ScanResult, SkippedRule

_OUTAGE_REASONS: Final[frozenset[FallbackReason]] = frozenset(
    {"llm_unavailable", "llm_misconfigured"}
)


@dataclass
class ScanScratch:
    detected: list[RuleFinding] = field(default_factory=list)
    explained: list[ExplainedFinding] = field(default_factory=list)


@dataclass(frozen=True)
class ScanContext:
    conn: AsyncConnection
    tenant: TenantContext
    provider: LLMProvider
    explain_limit: int
    scratch: ScanScratch = field(default_factory=ScanScratch)


class ScanState(TypedDict, total=False):
    scan_id: uuid.UUID
    rules_skipped: list[SkippedRule]
    findings_detected: int
    findings_created: int
    findings_explained: int
    explanations_fallback: int
    explanations_fallback_by_reason: dict[FallbackReason, int]
    explanations_deferred: int


async def _load(state: ScanState, runtime: Runtime[ScanContext]) -> ScanState:
    await companies.require_company(runtime.context.conn, runtime.context.tenant)
    return {"scan_id": uuid.uuid4()}


async def _detect(state: ScanState, runtime: Runtime[ScanContext]) -> ScanState:
    context = runtime.context
    detected, skipped = await scans.run_rules(context.conn, context.tenant.company_id)
    context.scratch.detected = detected
    return {"rules_skipped": skipped, "findings_detected": len(detected)}


async def _persist_findings(state: ScanState, runtime: Runtime[ScanContext]) -> ScanState:
    context = runtime.context
    company_id = context.tenant.company_id
    created = await scans.store_new_findings(context.conn, company_id, context.scratch.detected)
    summary = DetectionSummary(
        scan_id=state["scan_id"],
        findings_detected=state["findings_detected"],
        findings_created=len(created),
        rules_skipped=state["rules_skipped"],
    )
    await scans.audit_scan(context.conn, company_id, summary, created)
    return {"findings_created": len(created)}


async def _explain(state: ScanState, runtime: Runtime[ScanContext]) -> ScanState:
    context = runtime.context
    company_id = context.tenant.company_id
    pending = await pending_explanations(context.conn, company_id, context.explain_limit)
    metadata = {"company_id": str(company_id), "scan_id": str(state["scan_id"])}
    explained = await _explain_until_outage(context.provider, pending, metadata)
    context.scratch.explained = explained
    by_reason = _fallbacks_by_reason(explained)
    fallback = sum(by_reason.values())
    return {
        "findings_explained": len(explained) - fallback,
        "explanations_fallback": fallback,
        "explanations_fallback_by_reason": by_reason,
        "explanations_deferred": len(pending) - len(explained),
    }


async def _explain_until_outage(
    provider: LLMProvider, pending: Sequence[PendingFinding], metadata: dict[str, str]
) -> list[ExplainedFinding]:
    explained: list[ExplainedFinding] = []
    for item in pending:
        result = await explain_finding(provider, item.facts, metadata)
        explained.append(ExplainedFinding(item.finding_id, result))
        if result.fallback_reason in _OUTAGE_REASONS:
            break
    return explained


def _fallbacks_by_reason(explained: Sequence[ExplainedFinding]) -> dict[FallbackReason, int]:
    counts: dict[FallbackReason, int] = dict.fromkeys(get_args(FallbackReason), 0)
    for item in explained:
        if item.result.fallback_reason is not None:
            counts[item.result.fallback_reason] += 1
    return counts


async def _persist_explanations(state: ScanState, runtime: Runtime[ScanContext]) -> ScanState:
    context = runtime.context
    await store_explanations(
        context.conn, context.tenant.company_id, state["scan_id"], context.scratch.explained
    )
    return {}


def _build() -> CompiledStateGraph[ScanState, ScanContext, ScanState, ScanState]:
    graph = StateGraph(ScanState, context_schema=ScanContext)
    nodes = {
        "load": _load,
        "detect": _detect,
        "persist_findings": _persist_findings,
        "explain": _explain,
        "persist_explanations": _persist_explanations,
    }
    previous = START
    for name, node in nodes.items():
        graph.add_node(name, node)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile()


SCAN_GRAPH: Final = _build()


async def run_scan(
    conn: AsyncConnection, tenant: TenantContext, provider: LLMProvider, explain_limit: int
) -> ScanResult:
    context = ScanContext(conn=conn, tenant=tenant, provider=provider, explain_limit=explain_limit)
    final_state = await SCAN_GRAPH.ainvoke({}, context=context)
    return ScanResult.model_validate(final_state)
