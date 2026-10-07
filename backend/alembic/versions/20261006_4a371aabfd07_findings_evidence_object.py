from collections.abc import Sequence

from alembic import op

revision: str = "4a371aabfd07"
down_revision: str | Sequence[str] | None = "9b4d2e7f1c38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_findings_evidence_object"), "findings", "jsonb_typeof(evidence) = 'object'"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_findings_evidence_object"), "findings", type_="check")
