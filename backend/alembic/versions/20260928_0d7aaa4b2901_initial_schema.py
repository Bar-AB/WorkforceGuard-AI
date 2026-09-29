"""Initial schema: tenant tables, constraints, and least-privilege roles.

Revision ID: 0d7aaa4b2901
Revises:
Create Date: 2026-09-28 14:30:39.838322
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0d7aaa4b2901"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_TABLES = (
    "companies",
    "employees",
    "users",
    "shifts",
    "attendance_events",
    "access_logs",
    "payroll_runs",
    "overtime_policies",
)
# Agents may only create rows in their initial state: status, decisions, actor and
# timestamps are left to column defaults or to a human acting through the API.
MCP_INSERT_COLUMNS = {
    "findings": (
        "company_id",
        "employee_id",
        "rule_id",
        "rule_version",
        "severity",
        "summary",
        "evidence",
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
# Agents must not see login identities or the eval ground truth they are scored against.
MCP_READABLE_TABLES = (
    *(t for t in SOURCE_TABLES if t != "users"),
    "findings",
    "proposed_corrections",
)
APP_WRITABLE_TABLES = (*SOURCE_TABLES, "findings", "proposed_corrections", "anomaly_labels")
ROLES = ("app_rw", "mcp_reader")


def _create_role_if_missing(role: str) -> None:
    """Roles are cluster-wide, so another database may already have created them."""
    op.execute(
        f"DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') "
        f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"
    )
    # A pre-existing role may carry stronger attributes; force them back down.
    op.execute(f"ALTER ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")


def _grant_role_privileges() -> None:
    for role in ROLES:
        _create_role_if_missing(role)
        op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {', '.join(APP_WRITABLE_TABLES)} TO app_rw"
    )
    # The audit trail is append-only, even for the API.
    op.execute("GRANT SELECT, INSERT ON audit_log TO app_rw")
    op.execute(f"GRANT SELECT ON {', '.join(MCP_READABLE_TABLES)} TO mcp_reader")
    for table, columns in MCP_INSERT_COLUMNS.items():
        op.execute(f"GRANT INSERT ({', '.join(columns)}) ON {table} TO mcp_reader")
    # INSERT ... RETURNING id needs SELECT on id; the rest of the audit trail stays hidden.
    op.execute("GRANT SELECT (id) ON audit_log TO mcp_reader")


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("name", name=op.f("uq_companies_name")),
    )
    op.create_table(
        "audit_log",
        sa.Column("actor", sa.Text(), server_default=sa.text("current_user"), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("rule_version", sa.Text(), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_audit_log_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        op.f("ix_audit_log_company_id_occurred_at"),
        "audit_log",
        ["company_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "employees",
        sa.Column("employee_number", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("manager_id", sa.Uuid(), nullable=True),
        sa.Column("hired_on", sa.Date(), nullable=False),
        sa.Column("terminated_on", sa.Date(), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "terminated_on IS NULL OR terminated_on >= hired_on",
            name=op.f("ck_employees_dates_order"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "manager_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_employees_manager"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_employees_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employees")),
        sa.UniqueConstraint(
            "company_id", "employee_number", name=op.f("uq_employees_company_id_employee_number")
        ),
        sa.UniqueConstraint("company_id", "id", name=op.f("uq_employees_company_id_id")),
    )
    op.create_table(
        "overtime_policies",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("max_daily_hours", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("max_weekly_hours", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_overtime_policies_effective_order"),
        ),
        sa.CheckConstraint("max_daily_hours > 0", name=op.f("ck_overtime_policies_daily_positive")),
        sa.CheckConstraint(
            "max_weekly_hours >= max_daily_hours",
            name=op.f("ck_overtime_policies_weekly_covers_daily"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_overtime_policies_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_overtime_policies")),
    )
    op.create_index(
        op.f("ix_overtime_policies_company_id_effective_from"),
        "overtime_policies",
        ["company_id", "effective_from"],
        unique=False,
    )
    op.create_table(
        "access_logs",
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("door", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint("direction IN ('in', 'out')", name=op.f("ck_access_logs_direction")),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_access_logs_employee"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_access_logs_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_logs")),
    )
    op.create_index(
        op.f("ix_access_logs_company_id_employee_id_occurred_at"),
        "access_logs",
        ["company_id", "employee_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "anomaly_labels",
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("anomaly_type", sa.Text(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("source_table", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "anomaly_type IN ('overtime_breach', 'buddy_punching', 'off_shift_access')",
            name=op.f("ck_anomaly_labels_anomaly_type"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_anomaly_labels_employee"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_anomaly_labels_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_labels")),
    )
    op.create_index(
        op.f("ix_anomaly_labels_company_id_employee_id_occurred_on"),
        "anomaly_labels",
        ["company_id", "employee_id", "occurred_on"],
        unique=False,
    )
    op.create_table(
        "attendance_events",
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), server_default=sa.text("'terminal'"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('clock_in', 'clock_out')", name=op.f("ck_attendance_events_event_type")
        ),
        sa.CheckConstraint(
            "source IN ('terminal', 'mobile', 'manual')", name=op.f("ck_attendance_events_source")
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_attendance_events_employee"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_attendance_events_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance_events")),
    )
    op.create_index(
        op.f("ix_attendance_events_company_id_employee_id_occurred_at"),
        "attendance_events",
        ["company_id", "employee_id", "occurred_at"],
        unique=False,
    )
    op.create_table(
        "findings",
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high')", name=op.f("ck_findings_severity")
        ),
        sa.CheckConstraint(
            "status IN ('open', 'confirmed', 'dismissed')", name=op.f("ck_findings_status")
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_findings_employee"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_findings_company")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
        sa.UniqueConstraint("company_id", "id", name=op.f("uq_findings_company_id_id")),
    )
    op.create_index(
        op.f("ix_findings_company_id_employee_id_detected_at"),
        "findings",
        ["company_id", "employee_id", "detected_at"],
        unique=False,
    )
    op.create_table(
        "payroll_runs",
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("regular_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("overtime_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("gross_pay", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint("gross_pay >= 0", name=op.f("ck_payroll_runs_gross_pay_non_negative")),
        sa.CheckConstraint("period_end >= period_start", name=op.f("ck_payroll_runs_period_order")),
        sa.CheckConstraint(
            "regular_hours >= 0 AND overtime_hours >= 0",
            name=op.f("ck_payroll_runs_hours_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_payroll_runs_employee"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_payroll_runs_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payroll_runs")),
        sa.UniqueConstraint(
            "company_id",
            "employee_id",
            "period_start",
            name=op.f("uq_payroll_runs_company_id_employee_id_period_start"),
        ),
    )
    op.create_table(
        "shifts",
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_shifts_ends_after_start")),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_shifts_employee"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_shifts_company")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shifts")),
    )
    op.create_index(
        op.f("ix_shifts_company_id_employee_id_starts_at"),
        "shifts",
        ["company_id", "employee_id", "starts_at"],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint("role IN ('admin', 'manager', 'auditor')", name=op.f("ck_users_role")),
        sa.ForeignKeyConstraint(
            ["company_id", "employee_id"],
            ["employees.company_id", "employees.id"],
            name=op.f("fk_users_employee"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_users_company")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("company_id", "email", name=op.f("uq_users_company_id_email")),
        sa.UniqueConstraint("company_id", "id", name=op.f("uq_users_company_id_id")),
    )
    op.create_table(
        "proposed_corrections",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("target_table", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("patch", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("proposed_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) "
            "AND (decided_at IS NULL) = (decided_by_user_id IS NULL)",
            name=op.f("ck_proposed_corrections_decision_matches_status"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_proposed_corrections_status"),
        ),
        sa.CheckConstraint(
            "target_table IN ('shifts', 'attendance_events', 'access_logs', 'payroll_runs')",
            name=op.f("ck_proposed_corrections_target_table"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "decided_by_user_id"],
            ["users.company_id", "users.id"],
            name=op.f("fk_proposed_corrections_decided_by"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id", "finding_id"],
            ["findings.company_id", "findings.id"],
            name=op.f("fk_proposed_corrections_finding"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_proposed_corrections_company")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_proposed_corrections")),
    )
    op.create_index(
        op.f("ix_proposed_corrections_company_id_status_created_at"),
        "proposed_corrections",
        ["company_id", "status", "created_at"],
        unique=False,
    )
    _grant_role_privileges()


def downgrade() -> None:
    # Roles stay: they are cluster-wide and may hold grants in other databases.
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {', '.join(ROLES)}")
    op.drop_index(
        op.f("ix_proposed_corrections_company_id_status_created_at"),
        table_name="proposed_corrections",
    )
    op.drop_table("proposed_corrections")
    op.drop_table("users")
    op.drop_index(op.f("ix_shifts_company_id_employee_id_starts_at"), table_name="shifts")
    op.drop_table("shifts")
    op.drop_table("payroll_runs")
    op.drop_index(op.f("ix_findings_company_id_employee_id_detected_at"), table_name="findings")
    op.drop_table("findings")
    op.drop_index(
        op.f("ix_attendance_events_company_id_employee_id_occurred_at"),
        table_name="attendance_events",
    )
    op.drop_table("attendance_events")
    op.drop_index(
        op.f("ix_anomaly_labels_company_id_employee_id_occurred_on"), table_name="anomaly_labels"
    )
    op.drop_table("anomaly_labels")
    op.drop_index(
        op.f("ix_access_logs_company_id_employee_id_occurred_at"), table_name="access_logs"
    )
    op.drop_table("access_logs")
    op.drop_index(
        op.f("ix_overtime_policies_company_id_effective_from"), table_name="overtime_policies"
    )
    op.drop_table("overtime_policies")
    op.drop_table("employees")
    op.drop_index(op.f("ix_audit_log_company_id_occurred_at"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("companies")
