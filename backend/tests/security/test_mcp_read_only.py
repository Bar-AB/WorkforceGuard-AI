import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from mcp import Client
from mcp.types import CallToolResult
from sqlalchemy import Connection, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import ProposedCorrection
from tests.db_helpers import SOURCE_TABLES
from tests.factories import (
    insert_access_log,
    insert_attendance_event,
    insert_company,
    insert_employee,
    insert_finding,
    insert_payroll_run,
    insert_shift,
)
from tests.mcp_helpers import open_mcp_client

START = datetime(2026, 1, 5, 8, tzinfo=UTC)
WINDOW: dict[str, object] = {
    "start": "2026-01-05T00:00:00+00:00",
    "end": "2026-01-12T00:00:00+00:00",
}
SET_ROLE = "SET LOCAL ROLE mcp_reader"


@dataclass
class _RoleGuardLog:
    recording: bool = False
    guarded: int = 0
    unguarded: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Seed:
    company_id: uuid.UUID
    finding_id: uuid.UUID
    shift_id: uuid.UUID


@pytest.fixture
def role_guard_log() -> Iterator[_RoleGuardLog]:
    log = _RoleGuardLog()

    def on_begin(conn: Connection) -> None:
        conn.info["mcp_role_set"] = False

    def before_execute(
        conn: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if not log.recording:
            return
        if statement == SET_ROLE:
            conn.info["mcp_role_set"] = True
        elif conn.info.get("mcp_role_set"):
            log.guarded += 1
        else:
            log.unguarded.append(statement)

    event.listen(Engine, "begin", on_begin)
    event.listen(Engine, "before_cursor_execute", before_execute)
    yield log
    event.remove(Engine, "before_cursor_execute", before_execute)
    event.remove(Engine, "begin", on_begin)


async def _commit_one_row_per_table(engine: AsyncEngine) -> _Seed:
    async with engine.begin() as setup:
        company_id = await insert_company(setup)
        employee_id = await insert_employee(setup, company_id)
        await insert_attendance_event(setup, company_id, employee_id, START)
        await insert_access_log(setup, company_id, employee_id, START)
        await insert_payroll_run(
            setup,
            company_id,
            employee_id,
            date(2026, 1, 1),
            date(2026, 1, 14),
            regular_hours=Decimal("80"),
            overtime_hours=Decimal("2"),
            gross_pay=Decimal("1000"),
        )
        return _Seed(
            company_id=company_id,
            finding_id=await insert_finding(setup, company_id, employee_id),
            shift_id=await insert_shift(setup, company_id, employee_id, START, 8),
        )


async def _snapshot_source_tables(engine: AsyncEngine) -> dict[str, str]:
    async with engine.connect() as conn:
        return {
            table: await conn.scalar(
                text(
                    "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY t.id), '')) "
                    f"FROM {table} t"
                )
            )
            for table in SOURCE_TABLES
        }


def _call_plan(seed: _Seed) -> list[tuple[str, dict[str, object]]]:
    proposal = {
        "finding_id": str(seed.finding_id),
        "target_table": "shifts",
        "target_id": str(seed.shift_id),
        "reason": "Shift ends an hour early.",
    }
    return [
        ("list_findings", {}),
        ("get_attendance_events", WINDOW),
        ("get_shifts", WINDOW),
        ("get_access_logs", WINDOW),
        ("get_payroll_summary", {"period_from": "2026-01-01", "period_to": "2026-01-31"}),
        ("propose_correction", {**proposal, "patch": {"ends_at": "2026-01-05T15:00:00Z"}}),
        ("propose_correction", {**proposal, "patch": {"company_id": str(uuid.uuid4())}}),
    ]


async def _call_all(
    client: Client, plan: list[tuple[str, dict[str, object]]]
) -> list[CallToolResult]:
    return [await client.call_tool(name, arguments) for name, arguments in plan]


async def test_every_tool_leaves_source_tables_unchanged(
    mcp_database_url: str, tool_engine: AsyncEngine, role_guard_log: _RoleGuardLog
) -> None:
    seed = await _commit_one_row_per_table(tool_engine)
    plan = _call_plan(seed)
    before = await _snapshot_source_tables(tool_engine)

    async with open_mcp_client(mcp_database_url, seed.company_id) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
        role_guard_log.recording = True
        results = await _call_all(client, plan)
        role_guard_log.recording = False

    after = await _snapshot_source_tables(tool_engine)
    async with tool_engine.connect() as check:
        corrections = await check.scalars(
            select(ProposedCorrection.id).where(ProposedCorrection.company_id == seed.company_id)
        )
        assert len(corrections.all()) == 1
    assert after == before
    assert {name for name, _ in plan} == listed
    assert [bool(result.is_error) for result in results] == [False] * 6 + [True]
    assert role_guard_log.unguarded == []
    assert role_guard_log.guarded > 0
