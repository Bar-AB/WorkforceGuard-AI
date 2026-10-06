"""Runs every detection rule for one company and stores what is new."""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AuditLog, Finding
from app.db.worked_time import load_overtime_limits, load_worked_days
from app.errors import ConflictError
from app.rules import overtime
from app.rules.findings import RuleFinding
from app.security.tenant import TenantContext
from app.services import companies

RuleRunner = Callable[[AsyncConnection, uuid.UUID], Awaitable[list[RuleFinding]]]


@dataclass(frozen=True)
class Rule:
    version: str
    run: RuleRunner


async def _run_overtime(conn: AsyncConnection, company_id: uuid.UUID) -> list[RuleFinding]:
    days = await load_worked_days(conn, company_id)
    return overtime.check_overtime(days, await load_overtime_limits(conn, company_id))


# Rule ids match the anomaly_labels.anomaly_type they are scored against in evals.
RULES: dict[str, Rule] = {overtime.RULE_ID: Rule(overtime.RULE_VERSION, _run_overtime)}


class SkippedRule(BaseModel):
    rule_id: str
    reason: str


class ScanResult(BaseModel):
    scan_id: uuid.UUID
    findings_detected: int
    findings_created: int
    # Rules the company's data could not be judged by (e.g. missing config); the rest still ran.
    rules_skipped: list[SkippedRule]


async def run_scan(conn: AsyncConnection, tenant: TenantContext) -> ScanResult:
    """Safe to re-run: a finding already stored is skipped, not duplicated."""
    await companies.require_company(conn, tenant)
    scan_id = uuid.uuid4()
    detected, skipped = await _run_rules(conn, tenant.company_id)
    created = await _store_new_findings(conn, tenant.company_id, detected)
    result = ScanResult(
        scan_id=scan_id,
        findings_detected=len(detected),
        findings_created=len(created),
        rules_skipped=skipped,
    )
    await _audit_scan(conn, tenant.company_id, result, created)
    return result


async def _run_rules(
    conn: AsyncConnection, company_id: uuid.UUID
) -> tuple[list[RuleFinding], list[SkippedRule]]:
    """A rule that cannot judge this company's data is reported as skipped, not fatal."""
    detected: list[RuleFinding] = []
    skipped: list[SkippedRule] = []
    for rule_id, rule in RULES.items():
        try:
            detected += await rule.run(conn, company_id)
        except ConflictError as error:
            skipped.append(SkippedRule(rule_id=rule_id, reason=str(error)))
    return detected, skipped


async def _store_new_findings(
    conn: AsyncConnection, company_id: uuid.UUID, findings: Sequence[RuleFinding]
) -> list[tuple[uuid.UUID, str]]:
    """Returns (id, rule_version) of the findings that were not stored before."""
    if not findings:
        return []
    statement = (
        pg_insert(Finding)
        .on_conflict_do_nothing(constraint="uq_findings_company_id_dedup_key")
        .returning(Finding.id, Finding.rule_version)
    )
    # A parameter list, not .values([...]): SQLAlchemy then batches the rows, so a big scan
    # never hits asyncpg's 32767 bind-parameter limit for one statement.
    rows = [_finding_row(company_id, finding) for finding in findings]
    return [(row.id, row.rule_version) for row in await conn.execute(statement, rows)]


def _finding_row(company_id: uuid.UUID, finding: RuleFinding) -> dict[str, object]:
    return {
        "company_id": company_id,
        "employee_id": finding.employee_id,
        "rule_id": finding.rule_id,
        "rule_version": finding.rule_version,
        "severity": finding.severity,
        "summary": finding.summary,
        "evidence": finding.evidence,
        "occurred_on": finding.occurred_on,
        "dedup_key": finding.dedup_key,
    }


async def _audit_scan(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    result: ScanResult,
    created: Sequence[tuple[uuid.UUID, str]],
) -> None:
    scan_row = {
        "company_id": company_id,
        "action": "scan.completed",
        "entity_type": "scan",
        "entity_id": result.scan_id,
        "rule_version": None,
        "evidence": {
            "rules": {rule_id: rule.version for rule_id, rule in RULES.items()},
            "findings_detected": result.findings_detected,
            "findings_created": result.findings_created,
            "rules_skipped": [skip.model_dump() for skip in result.rules_skipped],
        },
    }
    finding_rows = [
        {
            "company_id": company_id,
            "action": "finding.created",
            "entity_type": "finding",
            "entity_id": finding_id,
            "rule_version": rule_version,
            "evidence": {"scan_id": str(result.scan_id)},
        }
        for finding_id, rule_version in created
    ]
    await conn.execute(insert(AuditLog), [scan_row, *finding_rows])
