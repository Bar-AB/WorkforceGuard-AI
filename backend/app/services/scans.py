import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AuditLog, Finding
from app.db.worked_time import load_overtime_limits, load_worked_days
from app.errors import ConflictError, InvalidInputError
from app.llm.structured import FallbackReason
from app.rules import overtime
from app.rules.findings import RuleFinding

RuleRunner = Callable[[AsyncConnection, uuid.UUID], Awaitable[list[RuleFinding]]]


@dataclass(frozen=True)
class Rule:
    version: str
    run: RuleRunner


async def _run_overtime(conn: AsyncConnection, company_id: uuid.UUID) -> list[RuleFinding]:
    days = await load_worked_days(conn, company_id)
    return overtime.check_overtime(days, await load_overtime_limits(conn, company_id))


RULES: dict[str, Rule] = {overtime.RULE_ID: Rule(overtime.RULE_VERSION, _run_overtime)}


def require_known_rule(rule_id: str) -> None:
    if rule_id not in RULES:
        raise InvalidInputError("Unknown rule_id.")


class SkippedRule(BaseModel):
    rule_id: str
    reason: str


class DetectionSummary(BaseModel):
    scan_id: uuid.UUID
    findings_detected: int
    findings_created: int
    rules_skipped: list[SkippedRule]


class ScanResult(DetectionSummary):
    findings_explained: int
    explanations_fallback: int
    explanations_fallback_by_reason: dict[FallbackReason, int]
    explanations_deferred: int


async def run_rules(
    conn: AsyncConnection, company_id: uuid.UUID
) -> tuple[list[RuleFinding], list[SkippedRule]]:
    detected: list[RuleFinding] = []
    skipped: list[SkippedRule] = []
    for rule_id, rule in RULES.items():
        try:
            detected += await rule.run(conn, company_id)
        except ConflictError as error:
            skipped.append(SkippedRule(rule_id=rule_id, reason=str(error)))
    return detected, skipped


async def store_new_findings(
    conn: AsyncConnection, company_id: uuid.UUID, findings: Sequence[RuleFinding]
) -> list[tuple[uuid.UUID, str]]:
    if not findings:
        return []
    statement = (
        pg_insert(Finding)
        .on_conflict_do_nothing(constraint="uq_findings_company_id_dedup_key")
        .returning(Finding.id, Finding.rule_version)
    )
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


async def audit_scan(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    summary: DetectionSummary,
    created: Sequence[tuple[uuid.UUID, str]],
) -> None:
    scan_row = {
        "company_id": company_id,
        "action": "scan.completed",
        "entity_type": "scan",
        "entity_id": summary.scan_id,
        "rule_version": None,
        "evidence": {
            "rules": {rule_id: rule.version for rule_id, rule in RULES.items()},
            "findings_detected": summary.findings_detected,
            "findings_created": summary.findings_created,
            "rules_skipped": [skip.model_dump() for skip in summary.rules_skipped],
        },
    }
    finding_rows = [
        {
            "company_id": company_id,
            "action": "finding.created",
            "entity_type": "finding",
            "entity_id": finding_id,
            "rule_version": rule_version,
            "evidence": {"scan_id": str(summary.scan_id)},
        }
        for finding_id, rule_version in created
    ]
    await conn.execute(insert(AuditLog), [scan_row, *finding_rows])
