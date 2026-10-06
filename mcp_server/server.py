import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.errors import DomainError, NotFoundError
from app.security.mcp_login_guard import StartupError, require_unprivileged_login
from app.security.tenant import TenantContext
from app.services import (
    access_logs,
    attendance,
    companies,
    corrections,
    findings,
    payroll,
    shifts,
)
from app.services.bounds import DEFAULT_EVENT_ROWS, MAX_EVENT_ROWS, EventQuery
from mcp_server.db import reader_transaction
from mcp_server.settings import McpSettings

PROPOSER = "agent:mcp"
INSTRUCTIONS = (
    "Read-only access to one company's attendance, shifts, access logs, payroll totals and "
    "findings. Corrections can only be proposed; a human approves them."
)


EventLimit = Annotated[int, Field(ge=1, le=MAX_EVENT_ROWS)]


@dataclass(frozen=True)
class ServerState:
    engine: AsyncEngine
    tenant: TenantContext


@asynccontextmanager
async def _tool_scope(
    ctx: Context[ServerState],
) -> AsyncIterator[tuple[AsyncConnection, TenantContext]]:
    state = ctx.request_context.lifespan_context
    try:
        async with reader_transaction(state.engine) as conn:
            yield conn, state.tenant
    except DomainError as error:
        raise ToolError(str(error)) from error


async def _check_startup(engine: AsyncEngine, tenant: TenantContext) -> None:
    try:
        async with reader_transaction(engine) as conn:
            await require_unprivileged_login(conn)
            await companies.require_company(conn, tenant)
    except NotFoundError as error:
        raise StartupError(
            f"MCP_COMPANY_ID {tenant.company_id} is not a known company. Check the setting."
        ) from error


def create_server(settings: McpSettings) -> MCPServer[ServerState]:
    @asynccontextmanager
    async def lifespan(_server: MCPServer[ServerState]) -> AsyncIterator[ServerState]:
        engine = create_async_engine(settings.mcp_database_url.get_secret_value())
        tenant = TenantContext(settings.mcp_company_id)
        try:
            await _check_startup(engine, tenant)
            yield ServerState(engine=engine, tenant=tenant)
        finally:
            await engine.dispose()

    server = MCPServer("workforceguard", instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.tool()
    async def list_findings(
        ctx: Context[ServerState],
        limit: Annotated[int, Field(ge=1, le=findings.MAX_PAGE_SIZE)] = 50,
        cursor: str | None = None,
    ) -> findings.FindingsPage:
        """Stored findings for this company, newest first. Pass `next_cursor` back as `cursor`."""
        async with _tool_scope(ctx) as (conn, tenant):
            return await findings.list_findings(conn, tenant, limit, cursor)

    @server.tool()
    async def get_attendance_events(
        ctx: Context[ServerState],
        start: datetime,
        end: datetime,
        employee_id: uuid.UUID | None = None,
        limit: EventLimit = DEFAULT_EVENT_ROWS,
    ) -> attendance.AttendanceEventsPage:
        """Clock-in/out events in [start, end) (tz-aware, at most 31 days), oldest first."""
        async with _tool_scope(ctx) as (conn, tenant):
            return await attendance.list_attendance_events(
                conn, tenant, EventQuery(start, end, employee_id, limit)
            )

    @server.tool()
    async def get_shifts(
        ctx: Context[ServerState],
        start: datetime,
        end: datetime,
        employee_id: uuid.UUID | None = None,
        limit: EventLimit = DEFAULT_EVENT_ROWS,
    ) -> shifts.ShiftsPage:
        """Planned shifts starting in [start, end) (tz-aware, at most 31 days), earliest first."""
        async with _tool_scope(ctx) as (conn, tenant):
            return await shifts.list_shifts(
                conn, tenant, EventQuery(start, end, employee_id, limit)
            )

    @server.tool()
    async def get_access_logs(
        ctx: Context[ServerState],
        start: datetime,
        end: datetime,
        employee_id: uuid.UUID | None = None,
        limit: EventLimit = DEFAULT_EVENT_ROWS,
    ) -> access_logs.AccessLogsPage:
        """Door access events in [start, end) (tz-aware, at most 31 days), oldest first."""
        async with _tool_scope(ctx) as (conn, tenant):
            return await access_logs.list_access_logs(
                conn, tenant, EventQuery(start, end, employee_id, limit)
            )

    @server.tool()
    async def get_payroll_summary(
        ctx: Context[ServerState], period_from: date, period_to: date
    ) -> payroll.PayrollSummary:
        """Company totals per pay period starting between the dates (at most 31 days apart),
        plus each employee's regular and overtime hours. No per-employee pay."""
        async with _tool_scope(ctx) as (conn, tenant):
            return await payroll.get_payroll_summary(conn, tenant, period_from, period_to)

    @server.tool()
    async def propose_correction(  # noqa: PLR0913 - one argument per proposal field
        ctx: Context[ServerState],
        *,
        finding_id: uuid.UUID,
        target_table: corrections.CorrectionTarget,
        target_id: uuid.UUID,
        patch: dict[str, corrections.PatchValue],
        reason: str,
    ) -> corrections.ProposedCorrectionItem:
        """Propose (never apply) a change to one row of the finding's employee. `patch` maps
        column names to new values: ISO-8601 strings for dates and timestamps (timestamps with a
        time zone), JSON numbers for hours and pay, true/false for flags. Nothing is coerced.
        A human approves or rejects it later."""
        proposal = corrections.CorrectionProposal(
            finding_id=finding_id,
            target_table=target_table,
            target_id=target_id,
            patch=patch,
            reason=reason,
        )
        async with _tool_scope(ctx) as (conn, tenant):
            return await corrections.propose_correction(conn, tenant, proposal, PROPOSER)

    return server
