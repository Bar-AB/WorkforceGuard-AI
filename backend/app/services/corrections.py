import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Annotated, Any, Literal, NamedTuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Numeric,
    String,
    Table,
    exists,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import (
    ONE_OF,
    ONE_PENDING_CORRECTION_INDEX,
    AuditLog,
    Base,
    Finding,
    ProposedCorrection,
)
from app.errors import ConflictError, InvalidInputError, NotFoundError
from app.security.tenant import TenantContext
from app.services.bounds import check_supported_day

MAX_REASON_LENGTH = 2000
MAX_PATCH_TEXT_LENGTH = 1000
PENDING_CORRECTION_MESSAGE = "A fix for this finding is already waiting for a decision."
ROW_CHANGED_MESSAGE = "The row changed while the fix was being checked."

CorrectionTarget = Literal["shifts", "attendance_events", "access_logs", "payroll_runs"]
PatchText = Annotated[str, StringConstraints(max_length=MAX_PATCH_TEXT_LENGTH)]
PatchValue = PatchText | int | float | bool | None

_Given = str | int | float | bool
_LOCKED_COLUMNS = frozenset({"id", "company_id", "employee_id"})
_MAX_UTC_OFFSET = timedelta(hours=14)
_NUL = chr(0)
_TIMESTAMP_JSON: TypeAdapter[datetime] = TypeAdapter(datetime)
_ROW_VALUES_JSON: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


class PendingCorrectionError(ConflictError):
    pass


@dataclass(frozen=True)
class FixJudgement:
    supported: bool | None
    reason: str | None
    judged_by: str


class TargetRef(NamedTuple):
    table: CorrectionTarget
    id: uuid.UUID


class TargetRow(NamedTuple):
    employee_id: uuid.UUID
    values: dict[str, object]


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
    table = _patched_table(target_table, patch)
    return {
        key: _normalized_value(_editable_column(table, key), value) for key, value in patch.items()
    }


def update_values(
    target_table: CorrectionTarget, stored_patch: Mapping[str, object]
) -> dict[str, object]:
    table = _patched_table(target_table, stored_patch)
    return {
        key: _db_value(_editable_column(table, key), value) for key, value in stored_patch.items()
    }


def check_reason(reason: str) -> str:
    if _NUL in reason:
        raise InvalidInputError("reason must not contain NUL characters.")
    stripped = reason.strip()
    if not 1 <= len(stripped) <= MAX_REASON_LENGTH:
        raise InvalidInputError(f"reason must be 1 to {MAX_REASON_LENGTH} characters.")
    return stripped


async def propose_correction(  # noqa: PLR0913 - id and judgement are keyword-only graph extras
    conn: AsyncConnection,
    tenant: TenantContext,
    proposal: CorrectionProposal,
    proposed_by: str,
    *,
    correction_id: uuid.UUID | None = None,
    judgement: FixJudgement | None = None,
    checked_before: dict[str, object] | None = None,
) -> ProposedCorrectionItem:
    patch = validate_patch(proposal.target_table, proposal.patch)
    reason = check_reason(proposal.reason)
    employee_id = await require_correctable_finding(conn, tenant, proposal.finding_id)
    await require_no_pending_correction(conn, tenant, proposal.finding_id)
    before = await read_target_values(conn, tenant, proposal, employee_id)
    if checked_before is not None and before != checked_before:
        raise ConflictError(ROW_CHANGED_MESSAGE)
    values: dict[str, object] = {
        "company_id": tenant.company_id,
        "finding_id": proposal.finding_id,
        "target_table": proposal.target_table,
        "target_id": proposal.target_id,
        "patch": patch,
        "before": before,
        "reason": reason,
        "proposed_by": proposed_by,
    }
    if correction_id is not None:
        values["id"] = correction_id
    if judgement is not None:
        values |= _judgement_values(judgement)
    item = await _insert_correction(conn, values)
    await _audit_proposal(conn, tenant, item)
    return item


def _judgement_values(judgement: FixJudgement) -> dict[str, object]:
    return {
        "judge_supported": judgement.supported,
        "judge_reason": judgement.reason,
        "judged_by": judgement.judged_by,
    }


def _table(target_table: CorrectionTarget) -> Table:
    return Base.metadata.tables[target_table]


def _patched_table(target_table: CorrectionTarget, patch: Mapping[str, object]) -> Table:
    if not patch:
        raise InvalidInputError("patch must change at least one column.")
    return _table(target_table)


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


def _db_value(column: Column[Any], value: object) -> object:
    if value is None:
        return _normalized_value(column, None)
    if not isinstance(value, str | bool):
        raise InvalidInputError(f"{column.name} holds a stored value of the wrong type.")
    column_type = column.type
    if isinstance(column_type, DateTime):
        return _parsed_timestamp(column.name, value)
    if isinstance(column_type, Date):
        return _parsed_date(column.name, value)
    if isinstance(column_type, Numeric):
        return _parsed_amount(column.name, column_type, value)
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
    normalized: str = _TIMESTAMP_JSON.dump_python(_parsed_timestamp(name, value), mode="json")
    return normalized


def _parsed_timestamp(name: str, value: _Given) -> datetime:
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
    return parsed


def _calendar_date(name: str, value: _Given) -> str:
    return _parsed_date(name, value).isoformat()


def _parsed_date(name: str, value: _Given) -> date:
    message = f"{name} must be an ISO-8601 date (YYYY-MM-DD)."
    if not isinstance(value, str):
        raise InvalidInputError(message)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise InvalidInputError(message) from error
    check_supported_day(name, parsed)
    return parsed


@cache
def _decimal_adapter(precision: int | None, scale: int | None) -> TypeAdapter[Decimal]:
    return TypeAdapter(Annotated[Decimal, Field(ge=0, max_digits=precision, decimal_places=scale)])


def _amount_message(name: str, column_type: Numeric[Any]) -> str:
    whole_digits = (column_type.precision or 0) - (column_type.scale or 0)
    return (
        f"{name} must be a non-negative number with at most {whole_digits} digits before "
        f"and {column_type.scale} after the decimal point."
    )


def _amount(name: str, column_type: Numeric[Any], value: _Given) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInputError(_amount_message(name, column_type))
    return str(_parsed_amount(name, column_type, str(value)))


def _parsed_amount(name: str, column_type: Numeric[Any], value: _Given) -> Decimal:
    if not isinstance(value, str):
        raise InvalidInputError(_amount_message(name, column_type))
    adapter = _decimal_adapter(column_type.precision, column_type.scale)
    try:
        return adapter.validate_python(value)
    except ValidationError as error:
        raise InvalidInputError(_amount_message(name, column_type)) from error


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


async def require_correctable_finding(
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


async def require_no_pending_correction(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> None:
    pending = await conn.scalar(
        select(
            exists().where(
                ProposedCorrection.company_id == tenant.company_id,
                ProposedCorrection.finding_id == finding_id,
                ProposedCorrection.status == "pending",
            )
        )
    )
    if pending:
        raise PendingCorrectionError(PENDING_CORRECTION_MESSAGE)


async def read_target_values(
    conn: AsyncConnection,
    tenant: TenantContext,
    proposal: CorrectionProposal,
    employee_id: uuid.UUID,
) -> dict[str, object]:
    target = TargetRef(proposal.target_table, proposal.target_id)
    row = await read_target_row(conn, tenant, target, proposal.patch)
    if row is None:
        raise NotFoundError(f"Row {target.id} not found in {target.table}.")
    if row.employee_id != employee_id:
        raise InvalidInputError(
            f"Row {target.id} in {target.table} is not about the finding's employee."
        )
    return row.values


async def read_target_row(
    conn: AsyncConnection,
    tenant: TenantContext,
    target: TargetRef,
    columns: Iterable[str],
    *,
    lock: bool = False,
) -> TargetRow | None:
    table = _table(target.table)
    patched = [_editable_column(table, name) for name in columns]
    statement = select(table.c.employee_id, *patched).where(
        table.c.company_id == tenant.company_id, table.c.id == target.id
    )
    if lock:
        statement = statement.with_for_update(key_share=True)
    row = (await conn.execute(statement)).one_or_none()
    if row is None:
        return None
    values: dict[str, object] = _ROW_VALUES_JSON.dump_python(
        {column.name: row._mapping[column] for column in patched}, mode="json"
    )
    return TargetRow(row.employee_id, values)


async def _insert_correction(
    conn: AsyncConnection, values: dict[str, object]
) -> ProposedCorrectionItem:
    inserted = insert(ProposedCorrection).values(values).returning(*_ITEM_COLUMNS)
    try:
        async with conn.begin_nested():
            row = (await conn.execute(inserted)).one()
    except IntegrityError as error:
        if _violated_constraint(error) == ONE_PENDING_CORRECTION_INDEX:
            raise PendingCorrectionError(PENDING_CORRECTION_MESSAGE) from None
        raise
    return ProposedCorrectionItem.model_validate(row)


def _violated_constraint(error: IntegrityError) -> str | None:
    driver_error = getattr(error.orig, "driver_exception", None)
    name = getattr(driver_error, "constraint_name", None)
    return name if isinstance(name, str) else None


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
