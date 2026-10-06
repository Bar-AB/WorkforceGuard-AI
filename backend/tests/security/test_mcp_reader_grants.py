from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.security.mcp_grants import EXPECTED_READER_GRANTS, Grant

READER_GRANTS_IN_PUBLIC_SCHEMA = text(
    "SELECT 'relation', format('%I.%I', n.nspname, c.relname), '', a.privilege_type "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a "
    "WHERE n.nspname = 'public' AND a.grantee = 'mcp_reader'::regrole "
    "UNION ALL "
    "SELECT 'column', format('%I.%I', n.nspname, c.relname), at.attname, a.privilege_type "
    "FROM pg_attribute at JOIN pg_class c ON c.oid = at.attrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(at.attacl) a "
    "WHERE n.nspname = 'public' AND a.grantee = 'mcp_reader'::regrole AND NOT at.attisdropped "
    "UNION ALL "
    "SELECT 'schema', n.nspname, '', a.privilege_type "
    "FROM pg_namespace n, aclexplode(n.nspacl) a WHERE a.grantee = 'mcp_reader'::regrole"
)


async def test_expected_reader_grants_match_migrated_database(
    rollback_conn: AsyncConnection,
) -> None:
    rows = (await rollback_conn.execute(READER_GRANTS_IN_PUBLIC_SCHEMA)).all()

    assert {Grant(*row) for row in rows} == EXPECTED_READER_GRANTS


async def test_reader_grants_query_skips_dropped_columns(
    rollback_conn: AsyncConnection,
) -> None:
    for statement in (
        "CREATE TABLE wg_grants_dropped (kept int, gone int)",
        "GRANT SELECT (gone) ON wg_grants_dropped TO mcp_reader",
        "ALTER TABLE wg_grants_dropped DROP COLUMN gone",
    ):
        await rollback_conn.execute(text(statement))

    rows = (await rollback_conn.execute(READER_GRANTS_IN_PUBLIC_SCHEMA)).all()

    assert {Grant(*row) for row in rows} == EXPECTED_READER_GRANTS


def test_expected_reader_grants_cover_the_tool_writes() -> None:
    assert Grant("column", "public.findings", "dedup_key", "INSERT") in EXPECTED_READER_GRANTS
    assert Grant("column", "public.audit_log", "id", "SELECT") in EXPECTED_READER_GRANTS
    assert Grant("relation", "public.users", "", "SELECT") not in EXPECTED_READER_GRANTS
