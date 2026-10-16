import json
import logging
from collections.abc import Mapping, Sequence
from typing import Final

from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.llm.explain import FindingFacts
from app.llm.prompt_loader import Prompt, load_prompt
from app.llm.propose import CandidateRow, ProposedFix, proposal_payload
from app.llm.provider import NO_NUL_PATTERN, ChatMessage, LLMProvider
from app.llm.structured import LLMOutputError, complete_structured
from app.security.injection import wrap_untrusted
from app.services.corrections import FixJudgement

PROPOSAL_JUDGE_PROMPT: Final[Prompt] = load_prompt("judge_proposal", 1)
MAX_JUDGE_REASON_CHARS: Final = 600
UNJUDGED: Final = FixJudgement(supported=None, reason=None, judged_by=PROPOSAL_JUDGE_PROMPT.label)

logger = logging.getLogger(__name__)


class FixUnderReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str
    column: str
    value: str
    reason: str


class ProposalVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(min_length=1, max_length=MAX_JUDGE_REASON_CHARS, pattern=NO_NUL_PATTERN)
    supported: StrictBool


def judge_messages(
    facts: FindingFacts, rows: Sequence[CandidateRow], fix: FixUnderReview
) -> list[ChatMessage]:
    data = {**proposal_payload(facts, rows), "fix": fix.model_dump()}
    return [
        ChatMessage(role="system", content=PROPOSAL_JUDGE_PROMPT.text),
        ChatMessage(role="user", content=wrap_untrusted(json.dumps(data, sort_keys=True))),
    ]


async def judge_fix(
    provider: LLMProvider,
    facts: FindingFacts,
    rows: Sequence[CandidateRow],
    fix: ProposedFix,
    trace_metadata: Mapping[str, str],
) -> FixJudgement:
    messages = judge_messages(facts, rows, _under_review(fix))
    try:
        with tracing_context(metadata=PROPOSAL_JUDGE_PROMPT.trace_metadata(trace_metadata)):
            reply = await complete_structured(provider, messages, ProposalVerdict)
    except LLMOutputError as error:
        logger.warning("proposal judge unavailable: %s (%s)", error.reason, error.detail)
        return UNJUDGED
    verdict = reply.value
    return FixJudgement(
        supported=verdict.supported, reason=verdict.reason, judged_by=PROPOSAL_JUDGE_PROMPT.label
    )


def _under_review(fix: ProposedFix) -> FixUnderReview:
    [(column, value)] = fix.patch.items()
    return FixUnderReview(target=fix.alias, column=column, value=str(value), reason=fix.reason)
