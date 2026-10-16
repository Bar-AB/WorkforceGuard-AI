from typing import Final

CHECKPOINT_TABLES: Final[frozenset[str]] = frozenset(
    {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
)
CHECKPOINT_SCHEMA_VERSION: Final = 9


def is_alembic_managed(name: str | None, type_: str, parent_names: object) -> bool:
    return not (type_ == "table" and name in CHECKPOINT_TABLES)
