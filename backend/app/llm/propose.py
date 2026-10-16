import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.errors import InvalidInputError
from app.llm.explain import FindingFacts, finding_payload
from app.llm.prompt_loader import Prompt, load_prompt
from app.llm.provider import (
    NO_NUL_PATTERN,
    ChatMessage,
    LLMMisconfiguredError,
    LLMProvider,
    LLMUnavailableError,
)
from app.llm.structured import FallbackReason, LLMOutputError, complete_structured
from app.security.injection import wrap_untrusted
from app.services.corrections import CorrectionTarget, PatchValue, update_values, validate_patch

PROPOSE_PROMPT: Final[Prompt] = load_prompt("propose_correction", 1)
MAX_PROPOSAL_REASON_CHARS: Final = 500
INVALID_FIX_REASON: Final = "The AI could not suggest a valid fix."
NO_ROWS_REASON: Final = "No shift or attendance rows were found for that day."
NO_CHANGE_REASON: Final = "The AI suggested a value the row already has."
MODEL_UNAVAILABLE_MESSAGE: Final = "The AI model is unavailable. Try again later."
MODEL_MISCONFIGURED_MESSAGE: Final = "The AI model is not configured correctly."
PROPOSER_COLUMNS: Final[Mapping[CorrectionTarget, frozenset[str]]] = {
    "shifts": frozenset({"starts_at", "ends_at"}),
    "attendance_events": frozenset({"occurred_at", "event_type"}),
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidateRow:
    alias: str
    target_table: CorrectionTarget
    fields: Mapping[str, JsonValue]


@dataclass(frozen=True)
class ProposedFix:
    alias: str
    target_table: CorrectionTarget
    patch: dict[str, PatchValue]
    reason: str
    proposed_by: str


type NoFixReason = (
    FallbackReason
    | Literal[
        "no_change",
        "not_resolved",
        "moves_problem",
        "already_gone",
        "unchecked",
        "invalid_patch",
        "breaks_rule",
        "busy",
    ]
)


@dataclass(frozen=True)
class NoFix:
    reason: str
    fallback_reason: NoFixReason | None


type ProposeResult = ProposedFix | NoFix

_INVALID: Final = NoFix(reason=INVALID_FIX_REASON, fallback_reason="invalid_output")
_NO_CHANGE: Final = NoFix(reason=NO_CHANGE_REASON, fallback_reason="no_change")


class _ProposalReply(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["fix", "no_fix"]
    target: str = Field(max_length=20, pattern=NO_NUL_PATTERN)
    column: str = Field(max_length=40, pattern=NO_NUL_PATTERN)
    value: str = Field(max_length=100, pattern=NO_NUL_PATTERN)
    reason: str = Field(min_length=1, max_length=MAX_PROPOSAL_REASON_CHARS, pattern=NO_NUL_PATTERN)


def proposal_payload(facts: FindingFacts, rows: Sequence[CandidateRow]) -> dict[str, object]:
    return {
        "finding": finding_payload(facts),
        "rows": [
            {"alias": row.alias, "table": row.target_table, "fields": dict(row.fields)}
            for row in rows
        ],
    }


def _messages(facts: FindingFacts, rows: Sequence[CandidateRow]) -> list[ChatMessage]:
    data = wrap_untrusted(json.dumps(proposal_payload(facts, rows), sort_keys=True))
    return [
        ChatMessage(role="system", content=PROPOSE_PROMPT.text),
        ChatMessage(role="user", content=data),
    ]


def _checked_fix(reply: _ProposalReply, row: CandidateRow) -> dict[str, PatchValue] | None:
    if reply.column not in PROPOSER_COLUMNS.get(row.target_table, frozenset()):
        return None
    patch: dict[str, PatchValue] = {reply.column: reply.value}
    try:
        validate_patch(row.target_table, patch)
    except InvalidInputError:
        return None
    return patch


def _stored_value(target_table: CorrectionTarget, column: str, value: str) -> object:
    return update_values(target_table, validate_patch(target_table, {column: value}))[column]


def _changes_row(row: CandidateRow, column: str, value: str) -> bool:
    current = row.fields.get(column)
    if not isinstance(current, str):
        return True
    try:
        current_value = _stored_value(row.target_table, column, current)
    except InvalidInputError:
        return True
    return _stored_value(row.target_table, column, value) != current_value


def _to_result(reply: _ProposalReply, rows: Sequence[CandidateRow], model: str) -> ProposeResult:
    if reply.action == "no_fix":
        return NoFix(reason=reply.reason, fallback_reason=None)
    row = next((row for row in rows if row.alias == reply.target), None)
    patch = None if row is None else _checked_fix(reply, row)
    if row is None or patch is None:
        return _INVALID
    if not _changes_row(row, reply.column, reply.value):
        return _NO_CHANGE
    return ProposedFix(
        alias=row.alias,
        target_table=row.target_table,
        patch=patch,
        reason=reply.reason,
        proposed_by=f"llm:{model}:{PROPOSE_PROMPT.label}",
    )


def _raise_on_outage(error: LLMOutputError) -> None:
    if error.reason == "invalid_output":
        return
    logger.warning("correction proposer unavailable: %s (%s)", error.reason, error.detail)
    if error.reason == "llm_misconfigured":
        raise LLMMisconfiguredError(MODEL_MISCONFIGURED_MESSAGE)
    raise LLMUnavailableError(MODEL_UNAVAILABLE_MESSAGE)


async def propose_fix(
    provider: LLMProvider,
    facts: FindingFacts,
    rows: Sequence[CandidateRow],
    trace_metadata: Mapping[str, str],
) -> ProposeResult:
    if not rows:
        return NoFix(reason=NO_ROWS_REASON, fallback_reason=None)
    try:
        with tracing_context(metadata=PROPOSE_PROMPT.trace_metadata(trace_metadata)):
            reply = await complete_structured(provider, _messages(facts, rows), _ProposalReply)
    except LLMOutputError as error:
        _raise_on_outage(error)
        return _INVALID
    return _to_result(reply.value, rows, reply.model)
