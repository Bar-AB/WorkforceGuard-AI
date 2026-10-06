from collections.abc import Sequence

from alembic import op

revision: str = "0176fb6e4a65"
down_revision: str | Sequence[str] | None = "3f1e9b2c6a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mcp_server') "
        "THEN CREATE ROLE mcp_server LOGIN NOINHERIT; END IF; END $$"
    )
    op.execute(
        "ALTER ROLE mcp_server LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOBYPASSRLS NOREPLICATION"
    )
    op.execute("GRANT mcp_reader TO mcp_server WITH INHERIT FALSE, SET TRUE")
    op.execute("REVOKE ADMIN OPTION FOR mcp_reader FROM mcp_server")


def downgrade() -> None:
    pass
