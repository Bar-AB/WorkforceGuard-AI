import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Finding
from app.errors import ConflictError
from app.rules.findings import RuleFinding
from app.security.tenant import TenantContext
from app.services.correction_decisions import PatchTarget, apply_patch, is_lock_not_available
from app.services.corrections import (
    CorrectionProposal,
    read_target_values,
    require_correctable_finding,
    validate_patch,
)
from app.services.scans import RULES, Rule

ResolveCheck = Literal[
    "resolves",
    "still_detected",
    "moves_problem",
    "already_gone",
    "unchecked",
    "invalid_patch",
    "breaks_rule",
    "busy",
]
DetectionCheck = Literal["detected", "already_gone", "unchecked"]
PatchRefusal = Literal["invalid_patch", "busy"]
DataState = Literal["current data", "patched data"]
Detected = Mapping[str, RuleFinding]

NOT_RESOLVED_REASON: Final = "The suggested change would not resolve this finding."
MOVES_PROBLEM_REASON: Final = (
    "The suggested change would clear this finding but cause another one for the same employee."
)
ALREADY_GONE_REASON: Final = (
    "This finding no longer shows up in the data, so there is nothing to fix."
)
UNCHECKED_REASON: Final = (
    "This finding's rule cannot be checked right now, so a fix cannot be verified."
)
INVALID_PATCH_REASON: Final = "The suggested change is not valid for this row."
BREAKS_RULE_REASON: Final = (
    "The suggested change would leave data that this finding's rule cannot check."
)
ROW_BUSY_REASON: Final = "The row is locked by another update. Try again shortly."
GUARD_LOCK_TIMEOUT_MS: Final = 5000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolveResult:
    check: ResolveCheck
    before: dict[str, object]


@dataclass(frozen=True)
class FixEffect:
    target_still_detected: bool
    new_or_bigger: int

    @property
    def verdict(self) -> ResolveCheck:
        if self.target_still_detected:
            return "still_detected"
        return "moves_problem" if self.new_or_bigger else "resolves"


@dataclass(frozen=True)
class _Trial:
    rule: Rule
    dedup_key: str
    target: PatchTarget
    employee_id: uuid.UUID


def fix_effect(
    before: Detected, after: Detected, target_key: str, employee_id: uuid.UUID
) -> FixEffect:
    worse = [
        key
        for key, finding in after.items()
        if key != target_key
        and finding.employee_id == employee_id
        and _is_new_or_bigger(before.get(key), finding)
    ]
    return FixEffect(target_still_detected=target_key in after, new_or_bigger=len(worse))


def _is_new_or_bigger(before: RuleFinding | None, after: RuleFinding) -> bool:
    if before is None:
        return True
    old_hours, new_hours = _hours(before), _hours(after)
    return old_hours is None or new_hours is None or new_hours > old_hours


def _hours(finding: RuleFinding) -> Decimal | None:
    try:
        return Decimal(finding.evidence["hours"])
    except (KeyError, InvalidOperation):
        return None


async def check_finding_detected(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> DetectionCheck:
    await require_correctable_finding(conn, tenant, finding_id)
    rule_id, dedup_key = await _detected_as(conn, tenant, finding_id)
    rule = _known_rule(rule_id)
    if rule is None:
        return "unchecked"
    detected = await _detected(conn, tenant, rule, "current data")
    if detected is None:
        return "unchecked"
    return "detected" if dedup_key in detected else "already_gone"


async def check_fix_resolves(
    conn: AsyncConnection, tenant: TenantContext, proposal: CorrectionProposal
) -> ResolveResult:
    patch = validate_patch(proposal.target_table, proposal.patch)
    employee_id = await require_correctable_finding(conn, tenant, proposal.finding_id)
    before = await read_target_values(conn, tenant, proposal, employee_id)
    rule_id, dedup_key = await _detected_as(conn, tenant, proposal.finding_id)
    rule = _known_rule(rule_id)
    if rule is None:
        return ResolveResult(check="unchecked", before=before)
    target = PatchTarget(proposal.target_table, proposal.target_id, patch)
    savepoint = await conn.begin_nested()
    try:
        check = await _try_fix(conn, tenant, _Trial(rule, dedup_key, target, employee_id))
    finally:
        await savepoint.rollback()
    return ResolveResult(check=check, before=before)


async def _detected_as(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> tuple[str, str]:
    row = (
        await conn.execute(
            select(Finding.rule_id, Finding.dedup_key).where(
                Finding.company_id == tenant.company_id, Finding.id == finding_id
            )
        )
    ).one()
    return row.rule_id, row.dedup_key


def _known_rule(rule_id: str) -> Rule | None:
    rule = RULES.get(rule_id)
    if rule is None:
        logger.warning("correction guard cannot run the rule: unknown rule")
    return rule


async def _detected(
    conn: AsyncConnection, tenant: TenantContext, rule: Rule, data: DataState
) -> Detected | None:
    try:
        findings = await rule.run(conn, tenant.company_id)
    except ConflictError as error:
        logger.warning("correction guard cannot run the rule on %s: %s", data, type(error).__name__)
        return None
    return {finding.dedup_key: finding for finding in findings}


async def _try_fix(conn: AsyncConnection, tenant: TenantContext, trial: _Trial) -> ResolveCheck:
    before = await _detected(conn, tenant, trial.rule, "current data")
    if before is None:
        return "unchecked"
    if trial.dedup_key not in before:
        return "already_gone"
    refusal = await _apply_trial_patch(conn, tenant, trial)
    if refusal is not None:
        return refusal
    after = await _detected(conn, tenant, trial.rule, "patched data")
    if after is None:
        return "breaks_rule"
    return _logged_verdict(fix_effect(before, after, trial.dedup_key, trial.employee_id))


def _logged_verdict(effect: FixEffect) -> ResolveCheck:
    verdict = effect.verdict
    if verdict != "resolves":
        logger.warning(
            "correction guard verdict %s: target_still_detected=%s new_or_bigger=%d",
            verdict,
            effect.target_still_detected,
            effect.new_or_bigger,
        )
    return verdict


async def _apply_trial_patch(
    conn: AsyncConnection, tenant: TenantContext, trial: _Trial
) -> PatchRefusal | None:
    await conn.execute(select(func.set_config("lock_timeout", f"{GUARD_LOCK_TIMEOUT_MS}ms", True)))
    try:
        await apply_patch(conn, tenant, trial.target, trial.employee_id)
    except ConflictError as error:
        logger.warning("correction guard patch refused: %s", error)
        return "invalid_patch"
    except DBAPIError as error:
        if not is_lock_not_available(error):
            raise
        logger.warning("correction guard patch refused: row locked by another update")
        return "busy"
    return None
