import uuid
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)
from sqlalchemy import Boolean, Column, Date, DateTime, Numeric, String, Table, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import ONE_OF, AuditLog, Base, Finding, ProposedCorrection
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.security.tenant import TenantContext
from app.services.bounds import check_supported_day

MAX_REASON_LENGTH = 2000
MAX_PATCH_TEXT_LENGTH = 1000

CorrectionTarget = Literal["shifts", "attendance_events", "access_logs", "payroll_runs"]
PatchText = Annotated[str, StringConstraints(max_length=MAX_PATCH_TEXT_LENGTH)]
PatchValue = PatchText | int | float | bool | None

_Given = str | int | float | bool
_LOCKED_COLUMNS = frozenset({"id", "company_id", "employee_id"})
_MAX_UTC_OFFSET = timedelta(hours=14)
_NUL = chr(0)
_TIMESTAMP_JSON: TypeAdapter[datetime] = TypeAdapter(datetime)


class CorrectionProposal(BaseModel):
    finding_id: uuid.UUID
    target_table: CorrectionTarget
    target_id: uuid.UUID
    patch: dict[str, PatchValue]
    reason: str


class ProposedCorrectionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    finding_id: uuid.UUID
    target_table: str
    target_id: uuid.UUID
    patch: dict[str, object]
    reason: str
    status: str
    proposed_by: str
    created_at: datetime


_ITEM_COLUMNS = (
    ProposedCorrection.id,
    ProposedCorrection.finding_id,
    ProposedCorrection.target_table,
    ProposedCorrection.target_id,
    ProposedCorrection.patch,
    ProposedCorrection.reason,
    ProposedCorrection.status,
    ProposedCorrection.proposed_by,
    ProposedCorrection.created_at,
)


def validate_patch(
    target_table: CorrectionTarget, patch: Mapping[str, PatchValue]
) -> dict[str, object]:
    if not patch:
        raise InvalidInputError("patch must change at least one column.")
    table = _table(target_table)
    return {
        key: _normalized_value(_editable_column(table, key), value) for key, value in patch.items()
    }


def check_reason(reason: str) -> str:
    if _NUL in reason:
        raise InvalidInputError("reason must not contain NUL characters.")
    stripped = reason.strip()
    if not 1 <= len(stripped) <= MAX_REASON_LENGTH:
        raise InvalidInputError(f"reason must be 1 to {MAX_REASON_LENGTH} characters.")
    return stripped


async def propose_correction(
    conn: AsyncConnection, tenant: TenantContext, proposal: CorrectionProposal, proposed_by: str
) -> ProposedCorrectionItem:
    patch = validate_patch(proposal.target_table, proposal.patch)
    reason = check_reason(proposal.reason)
    employee_id = await _correctable_finding_employee(conn, tenant, proposal.finding_id)
    await _require_target_of_employee(
        conn, tenant, proposal.target_table, proposal.target_id, employee_id
    )
    item = await _insert_correction(
        conn,
        {
            "company_id": tenant.company_id,
            "finding_id": proposal.finding_id,
            "target_table": proposal.target_table,
            "target_id": proposal.target_id,
            "patch": patch,
            "reason": reason,
            "proposed_by": proposed_by,
        },
    )
    await _audit_proposal(conn, tenant, item)
    return item


def _table(target_table: CorrectionTarget) -> Table:
    return Base.metadata.tables[target_table]


def _editable_column(table: Table, key: str) -> Column[Any]:
    editable = [column.name for column in table.c if column.name not in _LOCKED_COLUMNS]
    if key not in editable:
        raise InvalidInputError(
            f"Only these columns of {table.name} can be patched: {', '.join(editable)}."
        )
    return table.c[key]


def _normalized_value(column: Column[Any], value: PatchValue) -> object:
    if value is None:
        if not column.nullable:
            raise InvalidInputError(f"{column.name} cannot be null.")
        return None
    return _typed_value(column, value)


def _typed_value(column: Column[Any], value: _Given) -> object:
    column_type = column.type
    if isinstance(column_type, DateTime):
        return _timestamp(column.name, value)
    if isinstance(column_type, Date):
        return _calendar_date(column.name, value)
    if isinstance(column_type, Numeric):
        return _amount(column.name, column_type, value)
    if isinstance(column_type, Boolean):
        return _flag(column.name, value)
    if isinstance(column_type, String):
        return _text(column, value)
    raise InvalidInputError(f"{column.name} cannot be patched.")


def _timestamp(name: str, value: _Given) -> str:
    message = f"{name} must be an ISO-8601 timestamp with a time zone."
    if not isinstance(value, str):
        raise InvalidInputError(message)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise InvalidInputError(message) from error
    offset = parsed.utcoffset()
    if offset is None:
        raise InvalidInputError(message)
    if abs(offset) > _MAX_UTC_OFFSET:
        raise InvalidInputError(f"{name} needs a UTC offset within 14 hours.")
    check_supported_day(name, parsed.date())
    normalized: str = _TIMESTAMP_JSON.dump_python(parsed, mode="json")
    return normalized


def _calendar_date(name: str, value: _Given) -> str:
    message = f"{name} must be an ISO-8601 date (YYYY-MM-DD)."
    if not isinstance(value, str):
        raise InvalidInputError(message)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise InvalidInputError(message) from error
    check_supported_day(name, parsed)
    return parsed.isoformat()


@cache
def _decimal_adapter(precision: int | None, scale: int | None) -> TypeAdapter[Decimal]:
    return TypeAdapter(Annotated[Decimal, Field(ge=0, max_digits=precision, decimal_places=scale)])


def _amount(name: str, column_type: Numeric[Any], value: _Given) -> str:
    whole_digits = (column_type.precision or 0) - (column_type.scale or 0)
    message = (
        f"{name} must be a non-negative number with at most {whole_digits} digits before "
        f"and {column_type.scale} after the decimal point."
    )
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInputError(message)
    adapter = _decimal_adapter(column_type.precision, column_type.scale)
    try:
        return str(adapter.validate_python(Decimal(str(value))))
    except ValidationError as error:
        raise InvalidInputError(message) from error


def _flag(name: str, value: _Given) -> bool:
    if not isinstance(value, bool):
        raise InvalidInputError(f"{name} must be true or false.")
    return value


def _text(column: Column[Any], value: _Given) -> str:
    if not isinstance(value, str) or len(value) > MAX_PATCH_TEXT_LENGTH:
        raise InvalidInputError(
            f"{column.name} must be text of at most {MAX_PATCH_TEXT_LENGTH} characters."
        )
    if _NUL in value:
        raise InvalidInputError(f"{column.name} must not contain NUL characters.")
    allowed: tuple[str, ...] | None = column.info.get(ONE_OF)
    if allowed is not None and value not in allowed:
        raise InvalidInputError(f"{column.name} must be one of: {', '.join(allowed)}.")
    return value


async def _correctable_finding_employee(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> uuid.UUID:
    finding = (
        await conn.execute(
            select(Finding.employee_id, Finding.status).where(
                Finding.company_id == tenant.company_id, Finding.id == finding_id
            )
        )
    ).one_or_none()
    if finding is None:
        raise NotFoundError(f"Finding {finding_id} not found.")
    employee_id, status = finding
    if status == "dismissed":
        raise ConflictError(f"Finding {finding_id} is dismissed; there is nothing to correct.")
    return employee_id


async def _require_target_of_employee(
    conn: AsyncConnection,
    tenant: TenantContext,
    target_table: CorrectionTarget,
    target_id: uuid.UUID,
    employee_id: uuid.UUID,
) -> None:
    table = _table(target_table)
    row_employee_id = await conn.scalar(
        select(table.c.employee_id).where(
            table.c.company_id == tenant.company_id, table.c.id == target_id
        )
    )
    if row_employee_id is None:
        raise NotFoundError(f"Row {target_id} not found in {target_table}.")
    if row_employee_id != employee_id:
        raise InvalidInputError(
            f"Row {target_id} in {target_table} is not about the finding's employee."
        )


async def _insert_correction(
    conn: AsyncConnection, values: dict[str, object]
) -> ProposedCorrectionItem:
    inserted = insert(ProposedCorrection).values(values).returning(*_ITEM_COLUMNS)
    return ProposedCorrectionItem.model_validate((await conn.execute(inserted)).one())


async def _audit_proposal(
    conn: AsyncConnection, tenant: TenantContext, item: ProposedCorrectionItem
) -> None:
    await conn.execute(
        insert(AuditLog).values(
            company_id=tenant.company_id,
            action="correction.proposed",
            entity_type="proposed_correction",
            entity_id=item.id,
            evidence={
                "finding_id": str(item.finding_id),
                "target_table": item.target_table,
                "target_id": str(item.target_id),
            },
        )
    )
