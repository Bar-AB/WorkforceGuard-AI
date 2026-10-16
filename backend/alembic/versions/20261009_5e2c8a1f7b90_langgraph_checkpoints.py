from collections.abc import Sequence

from alembic import op

revision: str = "5e2c8a1f7b90"
down_revision: str | Sequence[str] | None = "4a371aabfd07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SAVER_DATA_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
SAVER_TABLES = ("checkpoint_migrations", *SAVER_DATA_TABLES)
SAVER_SCHEMA_VERSION = 9

SAVER_DDL = (
    "CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)",
    """CREATE TABLE checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
)""",
    """CREATE TABLE checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
)""",
    """CREATE TABLE checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
)""",
    "CREATE INDEX checkpoints_thread_id_idx ON checkpoints(thread_id)",
    "CREATE INDEX checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id)",
    "CREATE INDEX checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id)",
)


def upgrade() -> None:
    for statement in SAVER_DDL:
        op.execute(statement)
    op.execute(
        f"INSERT INTO checkpoint_migrations (v) SELECT generate_series(0, {SAVER_SCHEMA_VERSION})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(SAVER_DATA_TABLES)} TO app_rw")
    op.execute("GRANT SELECT ON checkpoint_migrations TO app_rw")


def downgrade() -> None:
    op.execute(f"DROP TABLE {', '.join(SAVER_TABLES)}")
