import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, cast

from pydantic import BaseModel
from sqlalchemy import Table, func, insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AuditLog, Base, Finding, ProposedCorrection
from app.errors import ConflictError, NotFoundError
from app.security.tenant import TenantContext
from app.services.corrections import (
    CorrectionTarget,
    TargetRef,
    read_target_row,
    update_values,
)
from app.services.users import Approver

Verdict = Literal["approve", "reject"]

FINDING_BUSY_MESSAGE: Final = "The finding is locked by another update. Try again shortly."
STALE_ROW_MESSAGE: Final = (
    "The row changed since this fix was proposed. Reject it and ask for a new suggestion."
)
_LOCK_NOT_AVAILABLE: Final = "55P03"

_VIEW_COLUMNS = (
    ProposedCorrection.id,
    ProposedCorrection.finding_id,
    ProposedCorrection.target_table,
    ProposedCorrection.target_id,
    ProposedCorrection.patch,
    ProposedCorrection.before,
    ProposedCorrection.reason,
    ProposedCorrection.status,
    ProposedCorrection.proposed_by,
    ProposedCorrection.created_at,
    ProposedCorrection.decided_by_user_id,
    ProposedCorrection.decided_at,
    ProposedCorrection.judge_supported,
    ProposedCorrection.judge_reason,
    ProposedCorrection.judged_by,
)


class CorrectionView(BaseModel):
    id: uuid.UUID
    finding_id: uuid.UUID
    target_table: str
    target_id: uuid.UUID
    patch: dict[str, object]
    current: dict[str, object] | None
    reason: str
    status: str
    proposed_by: str
    created_at: datetime
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime | None
    before: dict[str, object] | None
    judge_supported: bool | None
    judge_reason: str | None
    judged_by: str | None


class SuggestOutcome(BaseModel):
    proposal: CorrectionView | None
    reason: str | None


@dataclass(frozen=True)
class PatchTarget:
    target_table: CorrectionTarget
    target_id: uuid.UUID
    patch: dict[str, object]


@dataclass(frozen=True)
class LockedCorrection:
    correction_id: uuid.UUID
    finding_id: uuid.UUID
    target_table: CorrectionTarget
    target_id: uuid.UUID
    patch: dict[str, object]
    before: dict[str, object] | None

    @property
    def target(self) -> TargetRef:
        return TargetRef(self.target_table, self.target_id)

    @property
    def patch_target(self) -> PatchTarget:
        return PatchTarget(self.target_table, self.target_id, self.patch)


async def list_corrections(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> list[CorrectionView]:
    await _require_finding(conn, tenant, finding_id)
    rows = await conn.execute(
        select(*_VIEW_COLUMNS)
        .where(
            ProposedCorrection.company_id == tenant.company_id,
            ProposedCorrection.finding_id == finding_id,
        )
        .order_by(ProposedCorrection.created_at.desc(), ProposedCorrection.id)
    )
    return [await _view(conn, tenant, row._asdict()) for row in rows.all()]


async def get_correction(
    conn: AsyncConnection, tenant: TenantContext, correction_id: uuid.UUID
) -> CorrectionView:
    row = (
        await conn.execute(
            select(*_VIEW_COLUMNS).where(
                ProposedCorrection.company_id == tenant.company_id,
                ProposedCorrection.id == correction_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"Correction {correction_id} not found.")
    return await _view(conn, tenant, row._asdict())


async def lock_pending_correction(
    conn: AsyncConnection, tenant: TenantContext, correction_id: uuid.UUID
) -> LockedCorrection:
    row = (
        await conn.execute(
            select(
                ProposedCorrection.finding_id,
                ProposedCorrection.target_table,
                ProposedCorrection.target_id,
                ProposedCorrection.patch,
                ProposedCorrection.before,
                ProposedCorrection.status,
            )
            .where(
                ProposedCorrection.company_id == tenant.company_id,
                ProposedCorrection.id == correction_id,
            )
            .with_for_update(key_share=True)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"Correction {correction_id} not found.")
    if row.status != "pending":
        raise ConflictError(f"Correction {correction_id} is already {row.status}.")
    return LockedCorrection(
        correction_id=correction_id,
        finding_id=row.finding_id,
        target_table=cast(CorrectionTarget, row.target_table),
        target_id=row.target_id,
        patch=row.patch,
        before=row.before,
    )


async def approve_correction(
    conn: AsyncConnection, tenant: TenantContext, locked: LockedCorrection, approver: Approver
) -> None:
    employee_id = await _lock_correctable_finding(conn, tenant, locked.finding_id)
    await _require_unchanged_target(conn, tenant, locked, employee_id)
    await apply_patch(conn, tenant, locked.patch_target, employee_id)
    await _mark_decided(conn, tenant, locked.correction_id, "approved", approver)
    confirmed = await _confirm_finding(conn, tenant, locked.finding_id)
    entries = [_decision_entry("correction.approved", locked), _applied_entry(locked)]
    if confirmed:
        entries.append(_confirmed_entry(locked))
    await _audit(conn, tenant, approver, entries)


async def reject_correction(
    conn: AsyncConnection, tenant: TenantContext, locked: LockedCorrection, approver: Approver
) -> None:
    await _mark_decided(conn, tenant, locked.correction_id, "rejected", approver)
    await _audit(conn, tenant, approver, [_decision_entry("correction.rejected", locked)])


def _target(target_table: str) -> Table:
    return Base.metadata.tables[target_table]


async def _require_finding(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> None:
    found = await conn.scalar(
        select(Finding.id).where(Finding.company_id == tenant.company_id, Finding.id == finding_id)
    )
    if found is None:
        raise NotFoundError(f"Finding {finding_id} not found.")


async def _view(
    conn: AsyncConnection, tenant: TenantContext, fields: dict[str, object]
) -> CorrectionView:
    current = await _current_values(conn, tenant, fields)
    return CorrectionView.model_validate({**fields, "current": current})


async def _current_values(
    conn: AsyncConnection, tenant: TenantContext, fields: dict[str, object]
) -> dict[str, object] | None:
    target = TargetRef(
        cast(CorrectionTarget, fields["target_table"]), cast(uuid.UUID, fields["target_id"])
    )
    row = await read_target_row(conn, tenant, target, cast(dict[str, object], fields["patch"]))
    return None if row is None else row.values


async def _lock_correctable_finding(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> uuid.UUID:
    statement = (
        select(Finding.employee_id, Finding.status)
        .where(Finding.company_id == tenant.company_id, Finding.id == finding_id)
        .with_for_update(key_share=True, nowait=True)
    )
    try:
        finding = (await conn.execute(statement)).one()
    except DBAPIError as error:
        if is_lock_not_available(error):
            raise ConflictError(FINDING_BUSY_MESSAGE) from error
        raise
    if finding.status == "dismissed":
        raise ConflictError("The finding is dismissed; the fix cannot be applied.")
    employee_id: uuid.UUID = finding.employee_id
    return employee_id


async def _require_unchanged_target(
    conn: AsyncConnection,
    tenant: TenantContext,
    locked: LockedCorrection,
    employee_id: uuid.UUID,
) -> None:
    row = await read_target_row(conn, tenant, locked.target, locked.patch, lock=True)
    if row is None or row.employee_id != employee_id:
        return
    if locked.before is None:
        await _store_before(conn, tenant, locked.correction_id, row.values)
    elif row.values != locked.before:
        raise ConflictError(STALE_ROW_MESSAGE)


async def _store_before(
    conn: AsyncConnection,
    tenant: TenantContext,
    correction_id: uuid.UUID,
    before: dict[str, object],
) -> None:
    await conn.execute(
        update(ProposedCorrection)
        .where(
            ProposedCorrection.company_id == tenant.company_id,
            ProposedCorrection.id == correction_id,
        )
        .values(before=before)
    )


async def apply_patch(
    conn: AsyncConnection, tenant: TenantContext, target: PatchTarget, employee_id: uuid.UUID
) -> None:
    table = _target(target.target_table)
    statement = (
        update(table)
        .where(
            table.c.company_id == tenant.company_id,
            table.c.id == target.target_id,
            table.c.employee_id == employee_id,
        )
        .values(update_values(target.target_table, target.patch))
        .returning(table.c.id)
    )
    try:
        async with conn.begin_nested():
            updated = await conn.scalar(statement)
    except IntegrityError as error:
        raise ConflictError(
            f"Applying this fix would break a data rule: {_constraint_name(error)}."
        ) from None
    if updated is None:
        raise ConflictError("The row to fix no longer exists for the finding's employee.")


def is_lock_not_available(error: DBAPIError) -> bool:
    return getattr(error.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE


def _constraint_name(error: DBAPIError) -> str:
    driver_error = getattr(error.orig, "driver_exception", None)
    return str(getattr(driver_error, "constraint_name", None) or "unknown rule")


async def _mark_decided(
    conn: AsyncConnection,
    tenant: TenantContext,
    correction_id: uuid.UUID,
    status: Literal["approved", "rejected"],
    approver: Approver,
) -> None:
    await conn.execute(
        update(ProposedCorrection)
        .where(
            ProposedCorrection.company_id == tenant.company_id,
            ProposedCorrection.id == correction_id,
        )
        .values(status=status, decided_by_user_id=approver.user_id, decided_at=func.now())
    )


async def _confirm_finding(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> bool:
    confirmed = await conn.scalar(
        update(Finding)
        .where(
            Finding.company_id == tenant.company_id,
            Finding.id == finding_id,
            Finding.status == "open",
        )
        .values(status="confirmed")
        .returning(Finding.id)
    )
    return confirmed is not None


def _decision_entry(action: str, locked: LockedCorrection) -> dict[str, object]:
    return {
        "action": action,
        "entity_type": "proposed_correction",
        "entity_id": locked.correction_id,
        "evidence": {"finding_id": str(locked.finding_id)},
    }


def _applied_entry(locked: LockedCorrection) -> dict[str, object]:
    return {
        "action": "correction.applied",
        "entity_type": locked.target_table,
        "entity_id": locked.target_id,
        "evidence": {"correction_id": str(locked.correction_id), "columns": sorted(locked.patch)},
    }


def _confirmed_entry(locked: LockedCorrection) -> dict[str, object]:
    return {
        "action": "finding.confirmed",
        "entity_type": "finding",
        "entity_id": locked.finding_id,
        "evidence": {"correction_id": str(locked.correction_id)},
    }


async def _audit(
    conn: AsyncConnection,
    tenant: TenantContext,
    approver: Approver,
    entries: list[dict[str, object]],
) -> None:
    actor = f"user:{approver.user_id}"
    await conn.execute(
        insert(AuditLog),
        [{"company_id": tenant.company_id, "actor": actor, **entry} for entry in entries],
    )
