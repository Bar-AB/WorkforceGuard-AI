from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3b7e1d9a6c52"
down_revision: str | Sequence[str] | None = "8d3f6b2a9c41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "proposed_corrections"
JUDGE_CHECK = "ck_proposed_corrections_judge_matches_judged_by"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("before", postgresql.JSONB(), nullable=True))
    op.add_column(TABLE, sa.Column("judge_supported", sa.Boolean(), nullable=True))
    op.add_column(TABLE, sa.Column("judge_reason", sa.Text(), nullable=True))
    op.add_column(TABLE, sa.Column("judged_by", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f(JUDGE_CHECK),
        TABLE,
        "(judge_supported IS NULL) = (judge_reason IS NULL) "
        "AND (judge_supported IS NULL OR judged_by IS NOT NULL)",
    )
    op.execute(f"GRANT INSERT (before) ON {TABLE} TO mcp_reader")


def downgrade() -> None:
    op.drop_constraint(op.f(JUDGE_CHECK), TABLE, type_="check")
    op.drop_column(TABLE, "judged_by")
    op.drop_column(TABLE, "judge_reason")
    op.drop_column(TABLE, "judge_supported")
    op.drop_column(TABLE, "before")
