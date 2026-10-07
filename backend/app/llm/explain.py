import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal

from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.llm.prompt_loader import Prompt, load_prompt
from app.llm.provider import NO_NUL_PATTERN, ChatMessage, LLMProvider
from app.llm.structured import FallbackReason, LLMOutputError, complete_structured
from app.rules import overtime
from app.security.injection import wrap_untrusted

EXPLAIN_PROMPT: Final[Prompt] = load_prompt("explain_finding", 2)
MAX_EXPLANATION_CHARS: Final = 600
EVIDENCE_KEYS_BY_RULE: Final[Mapping[str, frozenset[str]]] = {
    overtime.RULE_ID: frozenset({"limit", "hours", "max_hours"}),
}

ExplanationSource = Literal["llm", "fallback"]


@dataclass(frozen=True)
class FindingFacts:
    rule_id: str
    severity: str
    occurred_on: date
    summary: str
    evidence: Mapping[str, JsonValue]


@dataclass(frozen=True)
class ExplainResult:
    text: str
    source: ExplanationSource
    prompt_label: str
    model_name: str | None
    fallback_reason: FallbackReason | None


class _Explanation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=MAX_EXPLANATION_CHARS, pattern=NO_NUL_PATTERN)


def _safe_evidence(facts: FindingFacts) -> dict[str, JsonValue]:
    allowed = EVIDENCE_KEYS_BY_RULE.get(facts.rule_id, frozenset())
    return {key: value for key, value in facts.evidence.items() if key in allowed}


def finding_payload(facts: FindingFacts) -> dict[str, object]:
    return {
        "rule_id": facts.rule_id,
        "severity": facts.severity,
        "occurred_on": facts.occurred_on.isoformat(),
        "evidence": _safe_evidence(facts),
    }


def _messages(facts: FindingFacts) -> list[ChatMessage]:
    data = wrap_untrusted(json.dumps(finding_payload(facts), sort_keys=True))
    return [
        ChatMessage(role="system", content=EXPLAIN_PROMPT.text),
        ChatMessage(role="user", content=data),
    ]


def _fallback(facts: FindingFacts, reason: FallbackReason) -> ExplainResult:
    pairs = ", ".join(f"{key}={value}" for key, value in sorted(_safe_evidence(facts).items()))
    return ExplainResult(
        text=f"{facts.summary} Evidence: {pairs or 'none'}.",
        source="fallback",
        prompt_label=EXPLAIN_PROMPT.label,
        model_name=None,
        fallback_reason=reason,
    )


async def explain_finding(
    provider: LLMProvider, facts: FindingFacts, trace_metadata: Mapping[str, str]
) -> ExplainResult:
    metadata = {
        **trace_metadata,
        "prompt_name": EXPLAIN_PROMPT.name,
        "prompt_version": str(EXPLAIN_PROMPT.version),
    }
    try:
        with tracing_context(metadata=metadata):
            reply = await complete_structured(provider, _messages(facts), _Explanation)
    except LLMOutputError as error:
        return _fallback(facts, error.reason)
    return ExplainResult(
        text=reply.value.text,
        source="llm",
        prompt_label=EXPLAIN_PROMPT.label,
        model_name=reply.model,
        fallback_reason=None,
    )
