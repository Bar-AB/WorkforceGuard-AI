from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c8a5647ab3d"
down_revision: str | Sequence[str] | None = "0d7aaa4b2901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("attendance_events", sa.Column("device_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("attendance_events", "device_id")
