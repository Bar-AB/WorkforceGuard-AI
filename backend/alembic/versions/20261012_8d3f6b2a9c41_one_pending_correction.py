from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d3f6b2a9c41"
down_revision: str | Sequence[str] | None = "5e2c8a1f7b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "uq_proposed_corrections_one_pending_per_finding"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "proposed_corrections",
        ["company_id", "finding_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="proposed_corrections")
