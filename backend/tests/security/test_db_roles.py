import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.factories import insert_company, insert_employee, insert_finding

SOURCE_TABLES = [
    "companies",
    "employees",
    "users",
    "shifts",
    "attendance_events",
    "access_logs",
    "payroll_runs",
    "overtime_policies",
]

MCP_READABLE_TABLES = [
    "companies",
    "employees",
    "shifts",
    "attendance_events",
    "access_logs",
    "payroll_runs",
    "overtime_policies",
    "findings",
    "proposed_corrections",
]

MCP_HIDDEN_TABLES = ["users", "anomaly_labels", "audit_log"]
MCP_INSERTABLE_TABLES = ["findings", "proposed_corrections", "audit_log"]


async def _has_privilege(conn: AsyncConnection, role: str, table: str, privilege: str) -> bool:
    result = await conn.execute(
        text("SELECT has_table_privilege(:role, :table, :privilege)"),
        {"role": role, "table": table, "privilege": privilege},
    )
    return bool(result.scalar_one())


async def _has_column_privilege(
    conn: AsyncConnection, role: str, table: str, privilege: str
) -> bool:
    result = await conn.execute(
        text("SELECT has_any_column_privilege(:role, :table, :privilege)"),
        {"role": role, "table": table, "privilege": privilege},
    )
    return bool(result.scalar_one())


@pytest.mark.parametrize("table", SOURCE_TABLES)
async def test_mcp_reader_update_source_table_is_denied(
    rollback_conn: AsyncConnection, table: str
) -> None:
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await rollback_conn.execute(text(f"UPDATE {table} SET id = id"))


@pytest.mark.parametrize("table", SOURCE_TABLES)
async def test_mcp_reader_delete_source_table_is_denied(
    rollback_conn: AsyncConnection, table: str
) -> None:
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await rollback_conn.execute(text(f"DELETE FROM {table}"))


@pytest.mark.parametrize("table", MCP_READABLE_TABLES)
async def test_mcp_reader_select_readable_table_is_allowed(
    rollback_conn: AsyncConnection, table: str
) -> None:
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    result = await rollback_conn.execute(text(f"SELECT count(*) FROM {table}"))

    assert result.scalar_one() >= 0


@pytest.mark.parametrize("table", MCP_HIDDEN_TABLES)
async def test_mcp_reader_select_hidden_table_is_denied(
    rollback_conn: AsyncConnection, table: str
) -> None:
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await rollback_conn.execute(text(f"SELECT * FROM {table}"))


@pytest.mark.parametrize("table", MCP_INSERTABLE_TABLES)
async def test_mcp_reader_has_column_insert_on_agent_output_table(
    rollback_conn: AsyncConnection, table: str
) -> None:
    assert await _has_column_privilege(rollback_conn, "mcp_reader", table, "INSERT")


@pytest.mark.parametrize("table", MCP_INSERTABLE_TABLES)
async def test_mcp_reader_has_no_update_on_agent_output_table(
    rollback_conn: AsyncConnection, table: str
) -> None:
    assert not await _has_column_privilege(rollback_conn, "mcp_reader", table, "UPDATE")


@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE", "TRUNCATE"])
async def test_app_rw_cannot_rewrite_audit_log(
    rollback_conn: AsyncConnection, privilege: str
) -> None:
    assert not await _has_privilege(rollback_conn, "app_rw", "audit_log", privilege)


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
async def test_app_rw_can_read_and_write_source_tables(
    rollback_conn: AsyncConnection, privilege: str
) -> None:
    assert await _has_privilege(rollback_conn, "app_rw", "attendance_events", privilege)


async def test_mcp_reader_insert_already_confirmed_finding_is_denied(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await rollback_conn.execute(
            text(
                "INSERT INTO findings (company_id, employee_id, rule_id, rule_version, "
                "severity, summary, status) "
                "VALUES (:c, :e, 'overtime', 'v1', 'high', 's', 'confirmed')"
            ),
            {"c": company_id, "e": employee_id},
        )


async def test_mcp_reader_insert_already_approved_correction_is_denied(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    finding_id = await insert_finding(rollback_conn, company_id, employee_id)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await rollback_conn.execute(
            text(
                "INSERT INTO proposed_corrections (company_id, finding_id, target_table, "
                "target_id, patch, reason, proposed_by, status) "
                "VALUES (:c, :f, 'shifts', gen_random_uuid(), '{}', 'r', 'agent', 'approved')"
            ),
            {"c": company_id, "f": finding_id},
        )


async def test_mcp_reader_insert_pending_correction_is_allowed(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    finding_id = await insert_finding(rollback_conn, company_id, employee_id)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    result = await rollback_conn.execute(
        text(
            "INSERT INTO proposed_corrections (company_id, finding_id, target_table, "
            "target_id, patch, reason, proposed_by) "
            "VALUES (:c, :f, 'shifts', gen_random_uuid(), '{}', 'r', 'agent') RETURNING status"
        ),
        {"c": company_id, "f": finding_id},
    )

    assert result.scalar_one() == "pending"


async def test_mcp_reader_insert_audit_log_with_actor_is_denied(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))

    with pytest.raises(ProgrammingError, match="permission denied"):
        await rollback_conn.execute(
            text(
                "INSERT INTO audit_log (company_id, actor, action, entity_type) "
                "VALUES (:c, 'user:admin', 'approve', 'proposed_correction')"
            ),
            {"c": company_id},
        )


async def test_mcp_reader_audit_log_actor_is_its_own_role(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    await rollback_conn.execute(text("SET LOCAL ROLE mcp_reader"))
    inserted = await rollback_conn.execute(
        text(
            "INSERT INTO audit_log (company_id, action, entity_type) "
            "VALUES (:c, 'propose', 'proposed_correction') RETURNING id"
        ),
        {"c": company_id},
    )
    audit_id: uuid.UUID = inserted.scalar_one()
    await rollback_conn.execute(text("RESET ROLE"))

    actor = await rollback_conn.execute(
        text("SELECT actor FROM audit_log WHERE id = :id"), {"id": audit_id}
    )

    assert actor.scalar_one() == "mcp_reader"


@pytest.mark.parametrize("role", ["app_rw", "mcp_reader"])
async def test_role_has_no_login_superuser_or_rls_bypass(
    rollback_conn: AsyncConnection, role: str
) -> None:
    result = await rollback_conn.execute(
        text(
            "SELECT rolcanlogin OR rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb "
            "FROM pg_roles WHERE rolname = :role"
        ),
        {"role": role},
    )

    assert result.scalar_one() is False
