import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.security.mcp_login_guard import StartupError, require_unprivileged_login

LOGIN = "CREATE ROLE {role} LOGIN NOINHERIT"
READER_GRANT = "GRANT mcp_reader TO {role} WITH INHERIT FALSE, SET TRUE"
ON_DATABASE = "DO $$ BEGIN EXECUTE format('{statement}', current_database()); END $$"
DATABASE_CREATE = ON_DATABASE.format(statement="GRANT CREATE ON DATABASE %I TO {grantee}")
LARGE_OBJECT_UPDATE = (
    "DO $$ BEGIN EXECUTE format('GRANT UPDATE ON LARGE OBJECT %s TO {role}', lo_create(0)); END $$"
)
UNSAFE_MEMBERSHIP = "its mcp_reader membership must be SET TRUE, INHERIT FALSE, ADMIN FALSE"
ADMIN_ATTRIBUTE = "the login has an admin attribute"
ONLY_READER = "the login's only role must be mcp_reader"


async def _run(conn: AsyncConnection, statements: list[str], role: str, admin: str) -> None:
    for statement in statements:
        await conn.execute(text(statement.format(role=role, admin=f'"{admin}"')))


async def _become_throwaway_role(conn: AsyncConnection, statements: list[str]) -> str:
    admin: str = (await conn.execute(text("SELECT session_user"))).scalar_one()
    role = f"wg_guard_{uuid.uuid4().hex[:8]}"
    await _run(conn, statements, role, admin)
    await conn.execute(text(f"SET LOCAL SESSION AUTHORIZATION {role}"))
    return role


@pytest.mark.parametrize(
    ("statements", "reason"),
    [
        pytest.param(
            ["CREATE ROLE {role} LOGIN INHERIT", "GRANT mcp_reader TO {role}"],
            UNSAFE_MEMBERSHIP,
            id="inherits-mcp-reader",
        ),
        pytest.param([f"{LOGIN} CREATEDB", READER_GRANT], ADMIN_ATTRIBUTE, id="createdb"),
        pytest.param([f"{LOGIN} CREATEROLE", READER_GRANT], ADMIN_ATTRIBUTE, id="createrole"),
        pytest.param([f"{LOGIN} BYPASSRLS", READER_GRANT], ADMIN_ATTRIBUTE, id="bypassrls"),
        pytest.param([f"{LOGIN} REPLICATION", READER_GRANT], ADMIN_ATTRIBUTE, id="replication"),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT app_rw TO {role} WITH INHERIT FALSE"],
            ONLY_READER,
            id="member-of-app-rw",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT {admin} TO {role} WITH INHERIT FALSE"],
            ONLY_READER,
            id="member-of-admin",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT pg_write_all_data TO {role} WITH INHERIT FALSE"],
            ONLY_READER,
            id="member-of-predefined-role",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT SELECT ON companies TO {role}"],
            "the login holds SELECT on relation public.companies",
            id="direct-table-grant",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT UPDATE ON payroll_runs TO {role}"],
            "the login holds UPDATE on relation public.payroll_runs",
            id="direct-update-on-payroll-runs",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT DELETE ON attendance_events TO {role}"],
            "the login holds DELETE on relation public.attendance_events",
            id="direct-delete-on-attendance-events",
        ),
        pytest.param(
            [
                LOGIN,
                READER_GRANT,
                "CREATE SEQUENCE {role}_seq",
                "GRANT USAGE ON SEQUENCE {role}_seq TO {role}",
            ],
            "the login holds USAGE on relation public.wg_guard_",
            id="direct-sequence-grant",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT CREATE ON SCHEMA public TO {role}"],
            "the login holds CREATE on schema public",
            id="schema-create",
        ),
        pytest.param(
            [
                LOGIN,
                READER_GRANT,
                "CREATE ROLE {role}_grantor",
                "GRANT mcp_reader TO {role}_grantor WITH ADMIN OPTION",
                "GRANT mcp_reader TO {role} WITH ADMIN TRUE, INHERIT FALSE "
                "GRANTED BY {role}_grantor",
            ],
            UNSAFE_MEMBERSHIP,
            id="admin-option-from-second-grantor",
        ),
        pytest.param([LOGIN], ONLY_READER, id="no-membership"),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT UPDATE (full_name) ON employees TO {role}"],
            "the login holds UPDATE on column public.employees full_name",
            id="column-update-on-employees",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT SELECT (email) ON users TO {role}"],
            "the login holds SELECT on column public.users email",
            id="column-select-on-users",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, DATABASE_CREATE.replace("{grantee}", "{role}")],
            "the login holds CREATE on database",
            id="database-create",
        ),
        pytest.param(
            [
                LOGIN,
                READER_GRANT,
                "CREATE FUNCTION {role}_definer() RETURNS int LANGUAGE sql SECURITY DEFINER "
                "AS 'SELECT 1'",
            ],
            "PUBLIC holds EXECUTE on function public.wg_guard_",
            id="security-definer-function-for-public",
        ),
        pytest.param(
            [
                LOGIN,
                READER_GRANT,
                "CREATE ROLE {role}_grantor",
                "GRANT mcp_reader TO {role}_grantor WITH ADMIN OPTION",
                "GRANT mcp_reader TO {role} WITH INHERIT FALSE, SET FALSE "
                "GRANTED BY {role}_grantor",
            ],
            UNSAFE_MEMBERSHIP,
            id="set-false-from-second-grantor",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT SET ON PARAMETER session_replication_role TO {role}"],
            "the login holds SET on parameter session_replication_role",
            id="parameter-set",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT ALTER SYSTEM ON PARAMETER archive_command TO {role}"],
            "the login holds ALTER SYSTEM on parameter archive_command",
            id="parameter-alter-system",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, LARGE_OBJECT_UPDATE],
            "the login holds UPDATE on large object",
            id="large-object-update",
        ),
        pytest.param(
            [LOGIN, READER_GRANT, "GRANT EXECUTE ON FUNCTION lo_export(oid, text) TO {role}"],
            "the login holds EXECUTE on function pg_catalog.lo_export(oid, text)",
            id="function-execute-on-lo-export",
        ),
    ],
)
async def test_require_unprivileged_login_refuses_privileged_role(
    rollback_conn: AsyncConnection, statements: list[str], reason: str
) -> None:
    await _become_throwaway_role(rollback_conn, statements)

    with pytest.raises(StartupError, match=re.escape(reason)):
        await require_unprivileged_login(rollback_conn)


@pytest.mark.parametrize(
    ("statement", "reason"),
    [
        pytest.param(
            "GRANT pg_write_all_data TO mcp_reader",
            "mcp_reader is a member of another role",
            id="reader-member-of-predefined-role",
        ),
        pytest.param(
            "GRANT UPDATE ON employees TO mcp_reader",
            "mcp_reader holds UPDATE on relation public.employees",
            id="reader-table-update",
        ),
        pytest.param(
            "ALTER ROLE mcp_reader BYPASSRLS",
            "mcp_reader has an admin attribute",
            id="reader-bypassrls",
        ),
        pytest.param(
            "GRANT UPDATE (gross_pay) ON payroll_runs TO mcp_reader",
            "mcp_reader holds UPDATE on column public.payroll_runs gross_pay",
            id="reader-column-update-on-payroll-runs",
        ),
        pytest.param(
            "GRANT INSERT (name) ON companies TO mcp_reader",
            "mcp_reader holds INSERT on column public.companies name",
            id="reader-column-insert-on-companies",
        ),
        pytest.param(
            "GRANT REFERENCES (full_name) ON employees TO mcp_reader",
            "mcp_reader holds REFERENCES on column public.employees full_name",
            id="reader-column-references-on-employees",
        ),
        pytest.param(
            "GRANT SELECT ON users TO mcp_reader",
            "mcp_reader holds SELECT on relation public.users",
            id="reader-select-on-users",
        ),
        pytest.param(
            "GRANT SELECT ON anomaly_labels TO mcp_reader",
            "mcp_reader holds SELECT on relation public.anomaly_labels",
            id="reader-select-on-anomaly-labels",
        ),
        pytest.param(
            DATABASE_CREATE.replace("{grantee}", "mcp_reader"),
            "mcp_reader holds CREATE on database",
            id="reader-database-create",
        ),
        pytest.param(
            "REVOKE SELECT ON shifts FROM mcp_reader",
            "mcp_reader lacks SELECT on relation public.shifts",
            id="reader-missing-expected-select",
        ),
        pytest.param(
            "GRANT SELECT ON companies TO PUBLIC",
            "PUBLIC holds SELECT on relation public.companies",
            id="public-select-on-companies",
        ),
        pytest.param(
            "GRANT SELECT (email) ON users TO PUBLIC",
            "PUBLIC holds SELECT on column public.users email",
            id="public-column-select-on-users",
        ),
        pytest.param(
            "ALTER TABLE overtime_policies OWNER TO mcp_reader",
            "mcp_reader holds DELETE on relation",
            id="reader-owns-table",
        ),
        pytest.param(
            "GRANT SELECT ON companies TO mcp_reader WITH GRANT OPTION",
            "mcp_reader holds SELECT WITH GRANT OPTION on relation public.companies",
            id="reader-select-with-grant-option",
        ),
        pytest.param(
            "GRANT INSERT (summary) ON findings TO mcp_reader WITH GRANT OPTION",
            "mcp_reader holds INSERT WITH GRANT OPTION on column public.findings summary",
            id="reader-column-insert-with-grant-option",
        ),
        pytest.param(
            "GRANT EXECUTE ON FUNCTION pg_read_binary_file(text) TO mcp_reader",
            "mcp_reader holds EXECUTE on function pg_catalog.pg_read_binary_file(text)",
            id="reader-execute-on-pg-read-binary-file",
        ),
        pytest.param(
            "GRANT ALTER SYSTEM ON PARAMETER archive_command TO PUBLIC",
            "PUBLIC holds ALTER SYSTEM on parameter archive_command",
            id="public-alter-system-on-parameter",
        ),
    ],
)
async def test_require_unprivileged_login_refuses_escalated_mcp_reader(
    rollback_conn: AsyncConnection, statement: str, reason: str
) -> None:
    await rollback_conn.execute(text(statement))
    await rollback_conn.execute(text("SET LOCAL SESSION AUTHORIZATION mcp_server"))

    with pytest.raises(StartupError, match=re.escape(reason)):
        await require_unprivileged_login(rollback_conn)


async def test_require_unprivileged_login_refuses_superuser(
    rollback_conn: AsyncConnection,
) -> None:
    with pytest.raises(StartupError, match=re.escape(ADMIN_ATTRIBUTE)):
        await require_unprivileged_login(rollback_conn)


async def test_require_unprivileged_login_refuses_logging_in_as_mcp_reader(
    rollback_conn: AsyncConnection,
) -> None:
    await rollback_conn.execute(text("SET LOCAL SESSION AUTHORIZATION mcp_reader"))

    with pytest.raises(StartupError, match=re.escape("the mcp_reader role must exist")):
        await require_unprivileged_login(rollback_conn)


async def test_require_unprivileged_login_allows_mcp_server(
    rollback_conn: AsyncConnection,
) -> None:
    await rollback_conn.execute(text("SET LOCAL SESSION AUTHORIZATION mcp_server"))

    await require_unprivileged_login(rollback_conn)


async def test_require_unprivileged_login_allows_mcp_server_shaped_role(
    rollback_conn: AsyncConnection,
) -> None:
    await _become_throwaway_role(rollback_conn, [LOGIN, READER_GRANT])

    await require_unprivileged_login(rollback_conn)


async def test_require_unprivileged_login_allows_database_hardened_against_public(
    rollback_conn: AsyncConnection,
) -> None:
    for statement in (
        "REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC",
        "GRANT CONNECT, TEMPORARY ON DATABASE %I TO mcp_server",
    ):
        await rollback_conn.execute(text(ON_DATABASE.format(statement=statement)))
    await rollback_conn.execute(text("SET LOCAL SESSION AUTHORIZATION mcp_server"))

    await require_unprivileged_login(rollback_conn)


async def test_require_unprivileged_login_ignores_dropped_column_grant(
    rollback_conn: AsyncConnection,
) -> None:
    for statement in (
        "CREATE TABLE wg_guard_dropped (kept int, gone int)",
        "GRANT SELECT (gone) ON wg_guard_dropped TO mcp_reader",
        "ALTER TABLE wg_guard_dropped DROP COLUMN gone",
        "SET LOCAL SESSION AUTHORIZATION mcp_server",
    ):
        await rollback_conn.execute(text(statement))

    await require_unprivileged_login(rollback_conn)


async def test_require_unprivileged_login_error_names_user_only(
    rollback_conn: AsyncConnection,
) -> None:
    role = await _become_throwaway_role(rollback_conn, [LOGIN])

    with pytest.raises(StartupError) as caught:
        await require_unprivileged_login(rollback_conn)

    assert role in str(caught.value)
    assert "MCP_DATABASE_URL" in str(caught.value)
    assert "://" not in str(caught.value)
