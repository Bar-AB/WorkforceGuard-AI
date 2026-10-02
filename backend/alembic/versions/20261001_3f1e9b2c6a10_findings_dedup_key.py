"""Add findings.occurred_on and findings.dedup_key so a re-run scan stores no duplicates.

Also indexes findings for the newest-first list.

Revision ID: 3f1e9b2c6a10
Revises: 7c8a5647ab3d
Create Date: 2026-10-01 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f1e9b2c6a10"
down_revision: str | Sequence[str] | None = "7c8a5647ab3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_COLUMNS = ("occurred_on", "dedup_key")


def upgrade() -> None:
    # No findings exist before this revision (nothing writes them yet), so NOT NULL is safe.
    op.add_column("findings", sa.Column("occurred_on", sa.Date(), nullable=False))
    op.add_column("findings", sa.Column("dedup_key", sa.Text(), nullable=False))
    op.create_unique_constraint(
        "uq_findings_company_id_dedup_key", "findings", ["company_id", "dedup_key"]
    )
    op.create_index(
        op.f("ix_findings_company_id_detected_at_id"),
        "findings",
        ["company_id", "detected_at", "id"],
    )
    op.execute(f"GRANT INSERT ({', '.join(NEW_COLUMNS)}) ON findings TO mcp_reader")


def downgrade() -> None:
    op.execute(f"REVOKE INSERT ({', '.join(NEW_COLUMNS)}) ON findings FROM mcp_reader")
    op.drop_index(op.f("ix_findings_company_id_detected_at_id"), table_name="findings")
    op.drop_constraint("uq_findings_company_id_dedup_key", "findings", type_="unique")
    op.drop_column("findings", "dedup_key")
    op.drop_column("findings", "occurred_on")
