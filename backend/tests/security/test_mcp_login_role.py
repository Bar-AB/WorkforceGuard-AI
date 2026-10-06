import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from mcp_server.db import reader_transaction
from tests.db_helpers import SOURCE_TABLES
from tests.factories import insert_company, insert_employee

AGENT_TABLES = ["findings", "proposed_corrections", "audit_log"]
ADMIN_USER = "<admin user>"


async def _become_mcp_server(conn: AsyncConnection) -> None:
    await conn.execute(text("SET LOCAL SESSION AUTHORIZATION mcp_server"))


@pytest.fixture
def admin_user(tool_database_url: str) -> str:
    user = make_url(tool_database_url).username
    assert user is not None
    return user


async def test_mcp_server_role_attributes(rollback_conn: AsyncConnection) -> None:
    result = await rollback_conn.execute(
        text(
            "SELECT rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb, "
            "rolbypassrls, rolreplication FROM pg_roles WHERE rolname = 'mcp_server'"
        )
    )

    assert result.one_or_none() == (True, False, False, False, False, False, False)


async def test_mcp_server_membership_is_mcp_reader_set_only(
    rollback_conn: AsyncConnection,
) -> None:
    result = await rollback_conn.execute(
        text(
            "SELECT g.rolname, m.inherit_option, m.set_option, m.admin_option "
            "FROM pg_auth_members m "
            "JOIN pg_roles g ON g.oid = m.roleid "
            "JOIN pg_roles u ON u.oid = m.member "
            "WHERE u.rolname = 'mcp_server'"
        )
    )
    rows = result.all()

    assert {row[0] for row in rows} == {"mcp_reader"}
    assert {tuple(row[1:]) for row in rows} == {(False, True, False)}


@pytest.mark.parametrize("table", [*SOURCE_TABLES, *AGENT_TABLES])
async def test_mcp_server_without_set_role_select_is_denied(
    rollback_conn: AsyncConnection, table: str
) -> None:
    await _become_mcp_server(rollback_conn)

    with pytest.raises(ProgrammingError, match="permission denied"):
        await rollback_conn.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


async def test_mcp_server_without_set_role_insert_finding_is_denied(
    rollback_conn: AsyncConnection,
) -> None:
    company_id = await insert_company(rollback_conn)
    employee_id = await insert_employee(rollback_conn, company_id)
    await _become_mcp_server(rollback_conn)

    with pytest.raises(ProgrammingError, match="permission denied"):
        await rollback_conn.execute(
            text(
                "INSERT INTO findings (company_id, employee_id, rule_id, rule_version, "
                "severity, summary, occurred_on, dedup_key) "
                "VALUES (:c, :e, 'overtime', 'v1', 'high', 's', '2026-01-06', 'k')"
            ),
            {"c": company_id, "e": employee_id},
        )


@pytest.mark.parametrize(
    "statement",
    ["SET ROLE app_rw", f"SET ROLE {ADMIN_USER}", f"SET SESSION AUTHORIZATION {ADMIN_USER}"],
)
async def test_mcp_server_set_role_escalation_is_denied(
    mcp_login_engine: AsyncEngine, admin_user: str, statement: str
) -> None:
    async with mcp_login_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permission denied"):
            await conn.execute(text(statement.replace(ADMIN_USER, f'"{admin_user}"')))


async def test_mcp_server_reset_role_leaves_no_table_access(
    mcp_login_engine: AsyncEngine,
) -> None:
    async with mcp_login_engine.connect() as conn:
        await conn.execute(text("SET ROLE mcp_reader"))
        await conn.execute(text("SELECT 1 FROM companies LIMIT 1"))
        await conn.execute(text("RESET ROLE"))

        with pytest.raises(ProgrammingError, match="permission denied"):
            await conn.execute(text("SELECT 1 FROM companies LIMIT 1"))


async def test_reader_transaction_as_mcp_server_reads_as_mcp_reader(
    mcp_login_engine: AsyncEngine,
) -> None:
    async with reader_transaction(mcp_login_engine) as conn:
        users = (await conn.execute(text("SELECT current_user, session_user"))).one()
        await conn.execute(text("SELECT count(*) FROM companies"))

    assert tuple(users) == ("mcp_reader", "mcp_server")
