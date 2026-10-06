import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import AuditLog, Finding, ProposedCorrection
from app.services.corrections import ProposedCorrectionItem
from tests.factories import insert_company, insert_employee, insert_finding, insert_shift
from tests.mcp_helpers import open_mcp_client, tool_error_text

START = datetime(2026, 1, 5, 8, tzinfo=UTC)


async def _commit_company_with_finding_and_shift(
    engine: AsyncEngine,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with engine.begin() as setup:
        company_id = await insert_company(setup)
        employee_id = await insert_employee(setup, company_id)
        finding_id = await insert_finding(setup, company_id, employee_id)
        shift_id = await insert_shift(setup, company_id, employee_id, START, 8)
    return company_id, finding_id, shift_id


def _arguments(finding_id: uuid.UUID, shift_id: uuid.UUID, patch: object) -> dict[str, object]:
    return {
        "finding_id": str(finding_id),
        "target_table": "shifts",
        "target_id": str(shift_id),
        "patch": patch,
        "reason": "Shift was planned one hour too long.",
    }


async def test_propose_correction_tool_returns_pending_item(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    company_id, finding_id, shift_id = await _commit_company_with_finding_and_shift(tool_engine)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "propose_correction",
            _arguments(finding_id, shift_id, {"ends_at": "2026-01-05T18:00:00+02:00"}),
        )

    item = ProposedCorrectionItem.model_validate(result.structured_content)
    assert (item.status, item.proposed_by, item.target_id) == ("pending", "agent:mcp", shift_id)
    assert item.patch == {"ends_at": "2026-01-05T18:00:00+02:00"}


async def test_propose_correction_tool_invalid_patch_is_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    company_id, finding_id, shift_id = await _commit_company_with_finding_and_shift(tool_engine)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "propose_correction",
            _arguments(finding_id, shift_id, {"company_id": str(uuid.uuid4())}),
        )

    assert "shifts can be patched: starts_at, ends_at" in tool_error_text(result)
    async with tool_engine.connect() as check:
        stored = await check.scalars(
            select(ProposedCorrection.id).where(ProposedCorrection.company_id == company_id)
        )
        assert stored.all() == []


async def test_propose_correction_tool_audit_actor_is_mcp_reader(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    company_id, finding_id, shift_id = await _commit_company_with_finding_and_shift(tool_engine)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        await client.call_tool(
            "propose_correction", _arguments(finding_id, shift_id, {"ends_at": None})
        )
        await client.call_tool(
            "propose_correction",
            _arguments(finding_id, shift_id, {"ends_at": "2026-01-05T15:00:00+00:00"}),
        )

    async with tool_engine.connect() as check:
        actors = await check.scalars(
            select(AuditLog.actor).where(AuditLog.company_id == company_id)
        )
        assert actors.all() == ["mcp_reader"]


async def test_propose_correction_tool_target_of_other_employee_is_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    company_id, finding_id, _ = await _commit_company_with_finding_and_shift(tool_engine)
    async with tool_engine.begin() as setup:
        other_employee_id = await insert_employee(setup, company_id)
        other_shift_id = await insert_shift(setup, company_id, other_employee_id, START, 8)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "propose_correction",
            _arguments(finding_id, other_shift_id, {"ends_at": "2026-01-05T15:00:00+00:00"}),
        )

    assert "employee" in tool_error_text(result)


async def test_propose_correction_tool_dismissed_finding_is_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    company_id, finding_id, shift_id = await _commit_company_with_finding_and_shift(tool_engine)
    async with tool_engine.begin() as setup:
        await setup.execute(
            update(Finding).where(Finding.id == finding_id).values(status="dismissed")
        )

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "propose_correction",
            _arguments(finding_id, shift_id, {"ends_at": "2026-01-05T15:00:00+00:00"}),
        )

    assert "dismissed" in tool_error_text(result)


async def test_propose_correction_tool_schema_caps_patch_text(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    async with tool_engine.begin() as setup:
        company_id = await insert_company(setup)

    async with open_mcp_client(mcp_database_url, company_id) as client:
        tools = (await client.list_tools()).tools

    schema = next(tool for tool in tools if tool.name == "propose_correction").input_schema
    assert '"maxLength": 1000' in json.dumps(schema["properties"]["patch"])


async def test_propose_correction_tool_nul_in_reason_is_domain_tool_error(
    mcp_database_url: str, tool_engine: AsyncEngine
) -> None:
    company_id, finding_id, shift_id = await _commit_company_with_finding_and_shift(tool_engine)
    arguments = _arguments(finding_id, shift_id, {"ends_at": "2026-01-05T15:00:00+00:00"})

    async with open_mcp_client(mcp_database_url, company_id) as client:
        result = await client.call_tool(
            "propose_correction", {**arguments, "reason": "too long\x00 shift"}
        )

    assert "reason must not contain NUL" in tool_error_text(result)
