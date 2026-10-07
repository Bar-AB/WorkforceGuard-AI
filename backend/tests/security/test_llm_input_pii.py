import uuid
from collections.abc import Iterator
from typing import get_type_hints

from langchain_core.tracers.base import BaseTracer
from langchain_core.tracers.schemas import Run
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Finding
from app.graph.scan_graph import SCAN_GRAPH, ScanContext, ScanState, run_scan
from app.rules.findings import RuleFinding
from app.security.tenant import TenantContext
from app.services.explanations import ExplainedFinding
from tests.factories import insert_company_with_long_day
from tests.fake_llm import FakeLLM

GRAPH_NODES = {"load", "detect", "persist_findings", "explain", "persist_explanations"}


class _RunCollector(BaseTracer):
    def __init__(self) -> None:
        super().__init__()
        self.roots: list[Run] = []

    def _persist_run(self, run: Run) -> None:
        self.roots.append(run)


def _all_runs(runs: list[Run]) -> Iterator[Run]:
    for run in runs:
        yield run
        yield from _all_runs(run.child_runs)


async def _tenant_with_long_day(conn: AsyncConnection) -> tuple[TenantContext, uuid.UUID]:
    company_id, employee_id = await insert_company_with_long_day(conn)
    return TenantContext(company_id=company_id), employee_id


async def _identity_markers(
    conn: AsyncConnection, tenant: TenantContext, employee_id: uuid.UUID
) -> list[str]:
    finding = (await conn.execute(select(Finding).filter_by(company_id=tenant.company_id))).one()
    return [str(employee_id), "Test Person", finding.summary, finding.evidence["source_id"]]


def _context(conn: AsyncConnection, tenant: TenantContext, fake: FakeLLM) -> ScanContext:
    return ScanContext(conn=conn, tenant=tenant, provider=fake, explain_limit=10)


async def test_llm_input_has_no_employee_identity(rollback_conn: AsyncConnection) -> None:
    tenant, employee_id = await _tenant_with_long_day(rollback_conn)
    fake = FakeLLM()

    await run_scan(rollback_conn, tenant, fake, 10)

    markers = await _identity_markers(rollback_conn, tenant, employee_id)
    [messages] = fake.calls
    assert messages[1].content.startswith("<data>")
    for message in messages:
        for marker in markers:
            assert marker not in message.content


async def test_scan_graph_state_holds_only_ids_and_counts(
    rollback_conn: AsyncConnection,
) -> None:
    tenant, _ = await _tenant_with_long_day(rollback_conn)
    allowed = set(get_type_hints(ScanState))

    snapshots = [
        snapshot
        async for snapshot in SCAN_GRAPH.astream(
            {}, context=_context(rollback_conn, tenant, FakeLLM()), stream_mode="values"
        )
    ]

    assert snapshots[-1]["findings_explained"] == 1
    for snapshot in snapshots:
        assert set(snapshot) <= allowed
        for value in snapshot.values():
            items = value if isinstance(value, list) else [value]
            assert not any(isinstance(i, RuleFinding | ExplainedFinding) for i in items)


async def test_scan_graph_traced_runs_carry_no_employee_identity(
    rollback_conn: AsyncConnection,
) -> None:
    tenant, employee_id = await _tenant_with_long_day(rollback_conn)
    collector = _RunCollector()

    await SCAN_GRAPH.ainvoke(
        {},
        context=_context(rollback_conn, tenant, FakeLLM()),
        config={"callbacks": [collector]},
    )

    runs = list(_all_runs(collector.roots))
    assert {run.name for run in runs} >= GRAPH_NODES
    markers = await _identity_markers(rollback_conn, tenant, employee_id)
    for run in runs:
        traced = repr(run.inputs) + repr(run.outputs) + repr(run.extra)
        for marker in markers:
            assert marker not in traced, (run.name, marker)
