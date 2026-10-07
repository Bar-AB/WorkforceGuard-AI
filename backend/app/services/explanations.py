import uuid
from collections.abc import Sequence
from datetime import date
from typing import Final, NamedTuple

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import Row, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import AuditLog, Finding
from app.llm.explain import ExplainResult, FindingFacts

_EVIDENCE: Final = TypeAdapter(dict[str, JsonValue])

type _PendingRow = Row[uuid.UUID, str, str, date, str, dict[str, object]]


class PendingFinding(NamedTuple):
    finding_id: uuid.UUID
    facts: FindingFacts


class ExplainedFinding(NamedTuple):
    finding_id: uuid.UUID
    result: ExplainResult


async def pending_explanations(
    conn: AsyncConnection, company_id: uuid.UUID, limit: int
) -> list[PendingFinding]:
    query = (
        select(
            Finding.id,
            Finding.rule_id,
            Finding.severity,
            Finding.occurred_on,
            Finding.summary,
            Finding.evidence,
        )
        .where(Finding.company_id == company_id, Finding.explanation.is_(None))
        .order_by(Finding.detected_at, Finding.id)
        .limit(limit)
        .with_for_update(skip_locked=True, key_share=True)
    )
    return [_pending(row) for row in await conn.execute(query)]


def _pending(row: _PendingRow) -> PendingFinding:
    return PendingFinding(
        finding_id=row.id,
        facts=FindingFacts(
            rule_id=row.rule_id,
            severity=row.severity,
            occurred_on=row.occurred_on,
            summary=row.summary,
            evidence=_EVIDENCE.validate_python(row.evidence),
        ),
    )


async def store_explanations(
    conn: AsyncConnection,
    company_id: uuid.UUID,
    scan_id: uuid.UUID,
    explained: Sequence[ExplainedFinding],
) -> None:
    stored = [item for item in explained if await _store_one(conn, company_id, item)]
    if stored:
        await conn.execute(insert(AuditLog), [_audit_row(company_id, scan_id, i) for i in stored])


async def _store_one(conn: AsyncConnection, company_id: uuid.UUID, item: ExplainedFinding) -> bool:
    statement = (
        update(Finding)
        .where(
            Finding.company_id == company_id,
            Finding.id == item.finding_id,
            Finding.explanation.is_(None),
        )
        .values(
            explanation=item.result.text,
            explanation_source=item.result.source,
            explanation_prompt_version=item.result.prompt_label,
        )
        .returning(Finding.id)
    )
    return (await conn.execute(statement)).first() is not None


def _audit_row(
    company_id: uuid.UUID, scan_id: uuid.UUID, item: ExplainedFinding
) -> dict[str, object]:
    return {
        "company_id": company_id,
        "action": "finding.explained",
        "entity_type": "finding",
        "entity_id": item.finding_id,
        "model_name": item.result.model_name,
        "evidence": {
            "scan_id": str(scan_id),
            "source": item.result.source,
            "prompt_version": item.result.prompt_label,
            "fallback_reason": item.result.fallback_reason,
        },
    }
