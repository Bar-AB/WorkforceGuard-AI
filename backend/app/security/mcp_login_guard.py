from collections import defaultdict
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.security.mcp_grants import EXPECTED_READER_GRANTS, SCHEMA, Grant

SYSTEM_SCHEMA = "(left(n.nspname, 3) = 'pg_' OR n.nspname = 'information_schema')"
ROLE_SHAPES = text(
    "SELECT r.rolname, "
    "r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication, "
    "ARRAY(SELECT DISTINCT g.rolname FROM pg_auth_members m "
    "JOIN pg_roles g ON g.oid = m.roleid WHERE m.member = r.oid ORDER BY 1), "
    "COALESCE((SELECT bool_or(m.admin_option OR m.inherit_option OR NOT m.set_option) "
    "FROM pg_auth_members m WHERE m.member = r.oid), false) "
    "FROM pg_roles r WHERE r.rolname IN (session_user, 'mcp_reader') "
    "ORDER BY r.rolname = 'mcp_reader'"
)
OBJECTS = (
    "SELECT 'relation' AS kind, format('%I.%I', n.nspname, c.relname) AS name, '' AS col, "
    "c.relowner AS owner, coalesce(c.relacl, "
    "acldefault((CASE c.relkind WHEN 'S' THEN 's' ELSE 'r' END)::\"char\", c.relowner)) AS acl, "
    f"{SYSTEM_SCHEMA} AS public_exempt "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "UNION ALL "
    "SELECT 'column', format('%I.%I', n.nspname, c.relname), a.attname, NULL, a.attacl, "
    f"{SYSTEM_SCHEMA} FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE a.attacl IS NOT NULL AND NOT a.attisdropped "
    "UNION ALL "
    "SELECT 'schema', n.nspname, '', n.nspowner, "
    f"coalesce(n.nspacl, acldefault('n', n.nspowner)), {SYSTEM_SCHEMA} FROM pg_namespace n "
    "UNION ALL "
    "SELECT 'database', '', '', d.datdba, coalesce(d.datacl, acldefault('d', d.datdba)), "
    "false FROM pg_database d WHERE d.datname = current_database() "
    "UNION ALL "
    "SELECT 'parameter', p.parname, '', NULL, p.paracl, false FROM pg_parameter_acl p "
    "UNION ALL "
    "SELECT 'large object', l.oid::text, '', l.lomowner, "
    "coalesce(l.lomacl, acldefault('L', l.lomowner)), false FROM pg_largeobject_metadata l "
    "UNION ALL "
    "SELECT 'function', format('%I.%I(%s)', n.nspname, p.proname, "
    "pg_get_function_identity_arguments(p.oid)), '', p.proowner, "
    "coalesce(p.proacl, acldefault('f', p.proowner)), NOT p.prosecdef "
    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
)
GRANTS = text(
    "WITH grantee(oid, kind) AS (VALUES "
    "((SELECT oid FROM pg_roles WHERE rolname = session_user), 'login'), "
    "('mcp_reader'::regrole::oid, 'reader'), (0::oid, 'public')), "
    f"object AS ({OBJECTS}) "
    "SELECT g.kind, o.kind, o.name, o.col, "
    "a.privilege_type || CASE WHEN a.is_grantable THEN ' WITH GRANT OPTION' ELSE '' END "
    "FROM object o CROSS JOIN LATERAL aclexplode(o.acl) a JOIN grantee g ON g.oid = a.grantee "
    "WHERE g.kind <> 'public' OR NOT o.public_exempt "
    "UNION "
    "SELECT g.kind, o.kind, o.name, o.col, 'OWNER' FROM object o JOIN grantee g ON g.oid = o.owner"
)
LOGIN_ALLOWED = frozenset(
    {Grant("database", "", "", "CONNECT"), Grant("database", "", "", "TEMPORARY")}
)
PUBLIC_ALLOWED = LOGIN_ALLOWED | {Grant("schema", SCHEMA, "", "USAGE")}
ROLES_NOT_FOUND = (
    "MCP_DATABASE_URL must log in as a role other than mcp_reader, and the mcp_reader role "
    "must exist. Use the mcp_server login from .env.mcp (make mcp-user)."
)


class StartupError(RuntimeError):
    pass


@dataclass(frozen=True)
class RoleShape:
    name: str
    has_admin_attribute: bool
    memberships: list[str]
    has_unsafe_membership_option: bool


@dataclass(frozen=True)
class Privileges:
    login: frozenset[Grant]
    reader: frozenset[Grant]
    public: frozenset[Grant]


async def _read_role_shapes(conn: AsyncConnection) -> tuple[RoleShape, RoleShape]:
    match (await conn.execute(ROLE_SHAPES)).all():
        case [login, reader]:
            return RoleShape(*login), RoleShape(*reader)
        case _:
            raise StartupError(ROLES_NOT_FOUND)


async def _read_privileges(conn: AsyncConnection) -> Privileges:
    by_grantee: defaultdict[str, set[Grant]] = defaultdict(set)
    for grantee, *grant in (await conn.execute(GRANTS)).all():
        by_grantee[grantee].add(Grant(*grant))
    return Privileges(
        login=frozenset(by_grantee["login"]),
        reader=frozenset(by_grantee["reader"]),
        public=frozenset(by_grantee["public"]),
    )


def _describe(grants: AbstractSet[Grant]) -> str:
    names = sorted(f"{g.privilege} on {g.kind} {g.name} {g.column}".rstrip() for g in grants)
    return ", ".join(names[:3])


def _first_violation(login: RoleShape, reader: RoleShape, privileges: Privileges) -> str | None:
    extra = privileges.reader - EXPECTED_READER_GRANTS
    missing = EXPECTED_READER_GRANTS - privileges.reader
    public = privileges.public - PUBLIC_ALLOWED
    login_grants = privileges.login - LOGIN_ALLOWED
    rules = (
        (login.has_admin_attribute, "the login has an admin attribute"),
        (login.memberships != ["mcp_reader"], "the login's only role must be mcp_reader"),
        (
            login.has_unsafe_membership_option,
            "its mcp_reader membership must be SET TRUE, INHERIT FALSE, ADMIN FALSE",
        ),
        (reader.has_admin_attribute, "mcp_reader has an admin attribute"),
        (bool(reader.memberships), "mcp_reader is a member of another role"),
        (bool(login_grants), f"the login holds {_describe(login_grants)}"),
        (bool(extra), f"mcp_reader holds {_describe(extra)}"),
        (bool(missing), f"mcp_reader lacks {_describe(missing)}"),
        (bool(public), f"PUBLIC holds {_describe(public)}"),
    )
    return next((reason for broken, reason in rules if broken), None)


async def require_unprivileged_login(conn: AsyncConnection) -> None:
    login, reader = await _read_role_shapes(conn)
    violation = _first_violation(login, reader, await _read_privileges(conn))
    if violation:
        raise StartupError(
            f"MCP_DATABASE_URL logs in as {login.name}, which is not the unprivileged "
            f"mcp_server login: {violation}. Use the mcp_server login from .env.mcp "
            "(make mcp-user)."
        )
