from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b4d2e7f1c38"
down_revision: str | Sequence[str] | None = "0176fb6e4a65"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_COLUMNS = ("explanation", "explanation_source", "explanation_prompt_version")


def upgrade() -> None:
    for column in NEW_COLUMNS:
        op.add_column("findings", sa.Column(column, sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_findings_explanation_source"),
        "findings",
        "explanation_source IN ('llm', 'fallback')",
    )
    op.create_check_constraint(
        op.f("ck_findings_explanation_complete"),
        "findings",
        "(explanation IS NULL) = (explanation_source IS NULL) "
        "AND (explanation IS NULL) = (explanation_prompt_version IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_findings_explanation_complete"), "findings", type_="check")
    op.drop_constraint(op.f("ck_findings_explanation_source"), "findings", type_="check")
    for column in reversed(NEW_COLUMNS):
        op.drop_column("findings", column)
