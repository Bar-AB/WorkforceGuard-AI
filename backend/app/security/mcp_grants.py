from collections.abc import Mapping
from typing import Final, NamedTuple

SCHEMA: Final = "public"
MCP_READABLE_TABLES: Final = (
    "companies",
    "employees",
    "shifts",
    "attendance_events",
    "access_logs",
    "payroll_runs",
    "overtime_policies",
    "findings",
    "proposed_corrections",
)
MCP_INSERT_COLUMNS: Final = {
    "findings": (
        "company_id",
        "employee_id",
        "rule_id",
        "rule_version",
        "severity",
        "summary",
        "evidence",
        "occurred_on",
        "dedup_key",
    ),
    "proposed_corrections": (
        "company_id",
        "finding_id",
        "target_table",
        "target_id",
        "patch",
        "reason",
        "proposed_by",
    ),
    "audit_log": (
        "company_id",
        "action",
        "entity_type",
        "entity_id",
        "rule_version",
        "model_name",
        "evidence",
    ),
}
MCP_SELECT_COLUMNS: Final = {"audit_log": ("id",)}


class Grant(NamedTuple):
    kind: str
    name: str
    column: str
    privilege: str


def _column_grants(columns: Mapping[str, tuple[str, ...]], privilege: str) -> set[Grant]:
    return {
        Grant("column", f"{SCHEMA}.{table}", column, privilege)
        for table, names in columns.items()
        for column in names
    }


EXPECTED_READER_GRANTS: Final = frozenset(
    {
        Grant("schema", SCHEMA, "", "USAGE"),
        *(Grant("relation", f"{SCHEMA}.{table}", "", "SELECT") for table in MCP_READABLE_TABLES),
        *_column_grants(MCP_INSERT_COLUMNS, "INSERT"),
        *_column_grants(MCP_SELECT_COLUMNS, "SELECT"),
    }
)
