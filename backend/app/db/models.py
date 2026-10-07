import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}

Hours = Numeric(6, 2)
Money = Numeric(12, 2)

ATTENDANCE_EVENT_TYPES = ("clock_in", "clock_out")
ATTENDANCE_SOURCES = ("terminal", "mobile", "manual")
ACCESS_DIRECTIONS = ("in", "out")
ONE_OF = "one_of"


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _same_tenant_fk(column: str, target: str, name: str) -> ForeignKeyConstraint:
    """Composite FK so a row can only point at a row of the same company."""
    return ForeignKeyConstraint(
        ["company_id", column], [f"{target}.company_id", f"{target}.id"], name=name
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class _Row:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())


class _TenantRow(_Row):
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", name="company"))


class Company(_Row, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Employee(_TenantRow, Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        UniqueConstraint("company_id", "employee_number"),
        _same_tenant_fk("manager_id", "employees", "manager"),
        CheckConstraint("terminated_on IS NULL OR terminated_on >= hired_on", name="dates_order"),
    )

    employee_number: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    manager_id: Mapped[uuid.UUID | None]
    hired_on: Mapped[date] = mapped_column(Date)
    terminated_on: Mapped[date | None] = mapped_column(Date)


class User(_TenantRow, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        UniqueConstraint("company_id", "email"),
        _same_tenant_fk("employee_id", "employees", "employee"),
        CheckConstraint(_in("role", ("admin", "manager", "auditor")), name="role"),
    )

    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    employee_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Shift(_TenantRow, Base):
    __tablename__ = "shifts"
    __table_args__ = (
        _same_tenant_fk("employee_id", "employees", "employee"),
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        Index(None, "company_id", "employee_id", "starts_at"),
    )

    employee_id: Mapped[uuid.UUID]
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AttendanceEvent(_TenantRow, Base):
    __tablename__ = "attendance_events"
    __table_args__ = (
        _same_tenant_fk("employee_id", "employees", "employee"),
        CheckConstraint(_in("event_type", ATTENDANCE_EVENT_TYPES), name="event_type"),
        CheckConstraint(_in("source", ATTENDANCE_SOURCES), name="source"),
        Index(None, "company_id", "employee_id", "occurred_at"),
    )

    employee_id: Mapped[uuid.UUID]
    event_type: Mapped[str] = mapped_column(Text, info={ONE_OF: ATTENDANCE_EVENT_TYPES})
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(
        Text, server_default=text("'terminal'"), info={ONE_OF: ATTENDANCE_SOURCES}
    )
    device_id: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)


class AccessLog(_TenantRow, Base):
    __tablename__ = "access_logs"
    __table_args__ = (
        _same_tenant_fk("employee_id", "employees", "employee"),
        CheckConstraint(_in("direction", ACCESS_DIRECTIONS), name="direction"),
        Index(None, "company_id", "employee_id", "occurred_at"),
    )

    employee_id: Mapped[uuid.UUID]
    door: Mapped[str] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text, info={ONE_OF: ACCESS_DIRECTIONS})
    granted: Mapped[bool]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PayrollRun(_TenantRow, Base):
    __tablename__ = "payroll_runs"
    __table_args__ = (
        _same_tenant_fk("employee_id", "employees", "employee"),
        UniqueConstraint("company_id", "employee_id", "period_start"),
        CheckConstraint("period_end >= period_start", name="period_order"),
        CheckConstraint("regular_hours >= 0 AND overtime_hours >= 0", name="hours_non_negative"),
        CheckConstraint("gross_pay >= 0", name="gross_pay_non_negative"),
    )

    employee_id: Mapped[uuid.UUID]
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    regular_hours: Mapped[Decimal] = mapped_column(Hours)
    overtime_hours: Mapped[Decimal] = mapped_column(Hours)
    gross_pay: Mapped[Decimal] = mapped_column(Money)


class OvertimePolicy(_TenantRow, Base):
    __tablename__ = "overtime_policies"
    __table_args__ = (
        CheckConstraint("max_daily_hours > 0", name="daily_positive"),
        CheckConstraint("max_weekly_hours >= max_daily_hours", name="weekly_covers_daily"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from", name="effective_order"
        ),
        Index(None, "company_id", "effective_from"),
    )

    name: Mapped[str] = mapped_column(Text)
    max_daily_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    max_weekly_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class Finding(_TenantRow, Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("company_id", "id"),
        # Lets a scan re-run without storing the same anomaly twice.
        UniqueConstraint("company_id", "dedup_key"),
        _same_tenant_fk("employee_id", "employees", "employee"),
        CheckConstraint(_in("severity", ("low", "medium", "high")), name="severity"),
        CheckConstraint(_in("status", ("open", "confirmed", "dismissed")), name="status"),
        CheckConstraint("jsonb_typeof(evidence) = 'object'", name="evidence_object"),
        CheckConstraint(_in("explanation_source", ("llm", "fallback")), name="explanation_source"),
        CheckConstraint(
            "(explanation IS NULL) = (explanation_source IS NULL) "
            "AND (explanation IS NULL) = (explanation_prompt_version IS NULL)",
            name="explanation_complete",
        ),
        Index(None, "company_id", "employee_id", "detected_at"),
        # Serves the newest-first findings list and its (detected_at, id) cursor.
        Index(None, "company_id", "detected_at", "id"),
    )

    employee_id: Mapped[uuid.UUID]
    rule_id: Mapped[str] = mapped_column(Text)
    rule_version: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'open'"))
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'"))
    occurred_on: Mapped[date] = mapped_column(Date)
    dedup_key: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_source: Mapped[str | None] = mapped_column(Text)
    explanation_prompt_version: Mapped[str | None] = mapped_column(Text)


class ProposedCorrection(_TenantRow, Base):
    __tablename__ = "proposed_corrections"
    __table_args__ = (
        _same_tenant_fk("finding_id", "findings", "finding"),
        _same_tenant_fk("decided_by_user_id", "users", "decided_by"),
        CheckConstraint(
            _in("target_table", ("shifts", "attendance_events", "access_logs", "payroll_runs")),
            name="target_table",
        ),
        CheckConstraint(_in("status", ("pending", "approved", "rejected")), name="status"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) "
            "AND (decided_at IS NULL) = (decided_by_user_id IS NULL)",
            name="decision_matches_status",
        ),
        Index(None, "company_id", "status", "created_at"),
    )

    finding_id: Mapped[uuid.UUID]
    target_table: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID]
    patch: Mapped[dict[str, object]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    proposed_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by_user_id: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(_TenantRow, Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index(None, "company_id", "occurred_at"),)

    # Defaults to the DB role so an agent cannot write rows in someone else's name.
    actor: Mapped[str] = mapped_column(Text, server_default=text("current_user"))
    action: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[uuid.UUID | None]
    rule_version: Mapped[str | None] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, object]] = mapped_column(JSONB, server_default=text("'{}'"))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AnomalyLabel(_TenantRow, Base):
    __tablename__ = "anomaly_labels"
    __table_args__ = (
        _same_tenant_fk("employee_id", "employees", "employee"),
        CheckConstraint(
            _in("anomaly_type", ("overtime_breach", "buddy_punching", "off_shift_access")),
            name="anomaly_type",
        ),
        Index(None, "company_id", "employee_id", "occurred_on"),
    )

    employee_id: Mapped[uuid.UUID]
    anomaly_type: Mapped[str] = mapped_column(Text)
    occurred_on: Mapped[date] = mapped_column(Date)
    source_table: Mapped[str | None] = mapped_column(Text)
    source_id: Mapped[uuid.UUID | None]
    notes: Mapped[str | None] = mapped_column(Text)
