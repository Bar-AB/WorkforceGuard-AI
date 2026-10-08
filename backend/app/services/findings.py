import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, select, tuple_
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.models import Finding
from app.errors import InvalidInputError, NotFoundError
from app.security.tenant import TenantContext

MAX_PAGE_SIZE = 100

Severity = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class FindingFilters:
    rule_id: str | None = None
    severity: Severity | None = None
    occurred_from: date | None = None
    occurred_to: date | None = None

    def __post_init__(self) -> None:
        if self.occurred_from and self.occurred_to and self.occurred_from > self.occurred_to:
            raise InvalidInputError("occurred_from must be on or before occurred_to.")


class FindingSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    employee_id: uuid.UUID
    rule_id: str
    rule_version: str
    severity: str
    status: str
    summary: str
    occurred_on: date
    detected_at: datetime


class FindingDetail(FindingSummary):
    evidence: dict[str, object]
    explanation: str | None
    explanation_source: str | None
    explanation_prompt_version: str | None


class FindingsPage(BaseModel):
    items: list[FindingSummary]
    next_cursor: str | None


_SUMMARY_COLUMNS = (
    Finding.id,
    Finding.employee_id,
    Finding.rule_id,
    Finding.rule_version,
    Finding.severity,
    Finding.status,
    Finding.summary,
    Finding.occurred_on,
    Finding.detected_at,
)


async def list_findings(
    conn: AsyncConnection,
    tenant: TenantContext,
    filters: FindingFilters,
    limit: int,
    cursor: str | None,
) -> FindingsPage:
    query = (
        select(*_SUMMARY_COLUMNS)
        .where(Finding.company_id == tenant.company_id, *_filter_clauses(filters))
        .order_by(Finding.detected_at.desc(), Finding.id.desc())
        .limit(limit + 1)
    )
    if cursor is not None:
        query = query.where(
            tuple_(Finding.detected_at, Finding.id) < tuple_(*_decode_cursor(cursor))
        )
    rows = (await conn.execute(query)).all()
    items = [FindingSummary.model_validate(row) for row in rows[:limit]]
    has_more = len(rows) > limit
    next_cursor = _encode_cursor(items[-1].detected_at, items[-1].id) if has_more else None
    return FindingsPage(items=items, next_cursor=next_cursor)


async def get_finding(
    conn: AsyncConnection, tenant: TenantContext, finding_id: uuid.UUID
) -> FindingDetail:
    row = (
        await conn.execute(
            select(
                *_SUMMARY_COLUMNS,
                Finding.evidence,
                Finding.explanation,
                Finding.explanation_source,
                Finding.explanation_prompt_version,
            ).where(Finding.company_id == tenant.company_id, Finding.id == finding_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"Finding {finding_id} not found.")
    return FindingDetail.model_validate(row)


def _filter_clauses(filters: FindingFilters) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if filters.rule_id is not None:
        clauses.append(Finding.rule_id == filters.rule_id)
    if filters.severity is not None:
        clauses.append(Finding.severity == filters.severity)
    if filters.occurred_from is not None:
        clauses.append(Finding.occurred_on >= filters.occurred_from)
    if filters.occurred_to is not None:
        clauses.append(Finding.occurred_on <= filters.occurred_to)
    return clauses


def _encode_cursor(detected_at: datetime, finding_id: uuid.UUID) -> str:
    raw = f"{detected_at.isoformat()}|{finding_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        detected_at, finding_id = base64.urlsafe_b64decode(cursor).decode().split("|")
        return _utc_instant(detected_at), uuid.UUID(finding_id)
    except (binascii.Error, UnicodeDecodeError, ValueError, OverflowError) as error:
        raise InvalidInputError("Invalid cursor.") from error


def _utc_instant(iso_text: str) -> datetime:
    moment = datetime.fromisoformat(iso_text)
    if moment.tzinfo is None:
        raise ValueError("Cursor time has no time zone.")
    return moment.astimezone(UTC)
