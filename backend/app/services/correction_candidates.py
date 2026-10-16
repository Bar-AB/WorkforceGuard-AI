import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Final

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Finding
from app.llm.explain import FindingFacts
from app.llm.propose import CandidateRow
from app.security.tenant import TenantContext
from app.services.attendance import AttendanceEventItem, list_attendance_events
from app.services.bounds import EARLIEST_DAY, LATEST_DAY, EventQuery
from app.services.corrections import CorrectionTarget, require_correctable_finding
from app.services.shifts import ShiftItem, list_shifts

MAX_CANDIDATE_ROWS: Final = 20
CANDIDATE_WINDOW_BEFORE: Final = timedelta(hours=2)
CANDIDATE_WINDOW_AFTER: Final = timedelta(hours=36)

_EVIDENCE: Final = TypeAdapter(dict[str, JsonValue])
_EARLIEST_MOMENT: Final = datetime.combine(EARLIEST_DAY, time(), tzinfo=UTC)
_LATEST_MOMENT: Final = datetime.combine(LATEST_DAY, time.max, tzinfo=UTC)


@dataclass(frozen=True)
class _Source:
    target_table: CorrectionTarget
    fields: Mapping[str, JsonValue]
    row_id: uuid.UUID

    def row(self, alias: str) -> CandidateRow:
        return CandidateRow(alias=alias, target_table=self.target_table, fields=self.fields)


@dataclass(frozen=True)
class CorrectionCandidates:
    facts: FindingFacts
    rows: tuple[CandidateRow, ...]
    targets: Mapping[str, uuid.UUID]


async def load_correction_candidates(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> CorrectionCandidates:
    employee_id = await require_correctable_finding(conn, tenant, finding_id)
    facts = await _finding_facts(conn, tenant, finding_id)
    query = _day_query(facts, employee_id)
    shifts = (await list_shifts(conn, tenant, query)).items
    events = (await list_attendance_events(conn, tenant, query)).items
    sources = [*map(_shift_source, shifts), *map(_event_source, events)][:MAX_CANDIDATE_ROWS]
    rows = tuple(source.row(f"row_{index}") for index, source in enumerate(sources, 1))
    return CorrectionCandidates(
        facts=facts,
        rows=rows,
        targets={row.alias: source.row_id for row, source in zip(rows, sources, strict=True)},
    )


async def _finding_facts(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> FindingFacts:
    row = (
        await conn.execute(
            select(
                Finding.rule_id,
                Finding.severity,
                Finding.occurred_on,
                Finding.summary,
                Finding.evidence,
            ).where(Finding.company_id == tenant.company_id, Finding.id == finding_id)
        )
    ).one()
    return FindingFacts(
        rule_id=row.rule_id,
        severity=row.severity,
        occurred_on=row.occurred_on,
        summary=row.summary,
        evidence=_EVIDENCE.validate_python(row.evidence),
    )


def _day_query(facts: FindingFacts, employee_id: uuid.UUID) -> EventQuery:
    day_start = datetime.combine(facts.occurred_on, time(), tzinfo=UTC)
    return EventQuery(
        start=max(day_start - CANDIDATE_WINDOW_BEFORE, _EARLIEST_MOMENT),
        end=min(day_start + CANDIDATE_WINDOW_AFTER, _LATEST_MOMENT),
        employee_id=employee_id,
        limit=MAX_CANDIDATE_ROWS,
    )


def _shift_source(shift: ShiftItem) -> _Source:
    fields: dict[str, JsonValue] = {
        "starts_at": shift.starts_at.isoformat(),
        "ends_at": shift.ends_at.isoformat(),
    }
    return _Source(target_table="shifts", fields=fields, row_id=shift.id)


def _event_source(event: AttendanceEventItem) -> _Source:
    fields: dict[str, JsonValue] = {
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
        "source": event.source,
    }
    return _Source(target_table="attendance_events", fields=fields, row_id=event.id)
