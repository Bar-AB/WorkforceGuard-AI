import json
import logging
from collections.abc import Sequence
from datetime import date
from typing import Final

import pytest
from langsmith import get_tracing_context
from pydantic import ValidationError

from app.llm import proposal_judge
from app.llm.explain import FindingFacts
from app.llm.prompt_loader import PROMPTS_DIR
from app.llm.proposal_judge import (
    MAX_JUDGE_REASON_CHARS,
    PROPOSAL_JUDGE_PROMPT,
    FixUnderReview,
    ProposalVerdict,
    judge_fix,
    judge_messages,
)
from app.llm.propose import CandidateRow, ProposedFix
from app.llm.provider import (
    ChatMessage,
    LLMMisconfiguredError,
    LLMResponse,
    LLMUnavailableError,
    ResponseSchema,
)
from app.security.injection import wrap_untrusted
from app.services.corrections import FixJudgement
from evals import proposal_judge as eval_proposal_judge
from tests.fake_llm import FakeLLM

SOURCE_ID = "0b6f3c1a-7d2e-4f5a-9c8b-1e2d3f4a5b6c"
FACTS = FindingFacts(
    rule_id="overtime_breach",
    severity="high",
    occurred_on=date(2026, 3, 2),
    summary="Worked 17.00 h, daily limit 12 h.",
    evidence={"hours": "17.00", "source_table": "attendance_events", "source_id": SOURCE_ID},
)
ROWS = [
    CandidateRow(
        alias="row_1",
        target_table="attendance_events",
        fields={"event_type": "clock_out", "occurred_at": "2026-03-03T01:00:00+00:00"},
    )
]
FIX = FixUnderReview(
    target="row_1",
    column="occurred_at",
    value="2026-03-02T17:00:00+00:00",
    reason="The clock-out is 8 hours after the shift end.",
)
EXPECTED_DATA = {
    "finding": {
        "rule_id": "overtime_breach",
        "severity": "high",
        "occurred_on": "2026-03-02",
        "evidence": {"hours": "17.00"},
    },
    "rows": [
        {
            "alias": "row_1",
            "table": "attendance_events",
            "fields": {"event_type": "clock_out", "occurred_at": "2026-03-03T01:00:00+00:00"},
        }
    ],
    "fix": {
        "target": "row_1",
        "column": "occurred_at",
        "value": "2026-03-02T17:00:00+00:00",
        "reason": "The clock-out is 8 hours after the shift end.",
    },
}


def test_judge_messages_match_eval_shape() -> None:
    system, user = judge_messages(FACTS, ROWS, FIX)

    assert (system.role, system.content) == ("system", PROPOSAL_JUDGE_PROMPT.text)
    assert user.role == "user"
    assert user.content == wrap_untrusted(json.dumps(EXPECTED_DATA, sort_keys=True))
    assert SOURCE_ID not in user.content


def test_judge_prompt_loads_from_app_prompts() -> None:
    expected = (PROMPTS_DIR / "judge_proposal.v1.md").read_text(encoding="utf-8")

    assert PROPOSAL_JUDGE_PROMPT.label == "judge_proposal.v1"
    assert PROPOSAL_JUDGE_PROMPT.text == expected


@pytest.mark.parametrize(
    "reason", ["", "   ", "\n\t", "bad\x00reason", "x" * (MAX_JUDGE_REASON_CHARS + 1)]
)
def test_proposal_verdict_rejects_invalid_reason(reason: str) -> None:
    with pytest.raises(ValidationError):
        ProposalVerdict(reason=reason, supported=True)


def test_proposal_verdict_accepts_reason_at_max_length() -> None:
    reason = "x" * MAX_JUDGE_REASON_CHARS

    assert ProposalVerdict(reason=reason, supported=False).reason == reason


@pytest.mark.parametrize("supported", ['"true"', "1", '"no"'])
def test_proposal_verdict_rejects_off_schema_supported(supported: str) -> None:
    with pytest.raises(ValidationError):
        ProposalVerdict.model_validate_json(f'{{"reason": "ok", "supported": {supported}}}')


def test_proposal_verdict_schema_keeps_boolean_supported() -> None:
    assert ProposalVerdict.model_json_schema()["properties"]["supported"] == {
        "title": "Supported",
        "type": "boolean",
    }


def test_proposal_verdict_and_fix_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ProposalVerdict.model_validate({"reason": "ok", "supported": True, "score": 1})
    with pytest.raises(ValidationError):
        FixUnderReview.model_validate({**FIX.model_dump(), "id": SOURCE_ID})


def test_eval_shares_live_judge_prompt_and_models() -> None:
    eval_names = vars(eval_proposal_judge)

    assert eval_names["PROPOSAL_JUDGE_PROMPT"] is proposal_judge.PROPOSAL_JUDGE_PROMPT
    assert eval_names["ProposalVerdict"] is proposal_judge.ProposalVerdict
    assert eval_proposal_judge.ProposalJudgeCase.model_fields["fix"].annotation is FixUnderReview
    assert eval_names["judge_messages"] is judge_messages


PROPOSED: Final = ProposedFix(
    alias="row_1",
    target_table="attendance_events",
    patch={"occurred_at": "2026-03-02T17:00:00+00:00"},
    reason="The clock-out is 8 hours after the shift end.",
    proposed_by="llm:fake-llm:propose_correction.v1",
)
TRACE: Final = {"company_id": "tenant"}
UNJUDGED: Final = FixJudgement(supported=None, reason=None, judged_by="judge_proposal.v1")


class MetadataRecordingLLM(FakeLLM):
    def __init__(self, *replies: str) -> None:
        super().__init__(*replies)
        self.metadata: list[dict[str, object]] = []

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        self.metadata.append(dict(get_tracing_context()["metadata"] or {}))
        return await super().complete(messages, schema)


@pytest.mark.parametrize("supported", [True, False])
async def test_judge_fix_returns_verdict_from_model(supported: bool) -> None:
    fake = FakeLLM(json.dumps({"supported": supported, "reason": "  Matches the shift end. "}))

    judgement = await judge_fix(fake, FACTS, ROWS, PROPOSED, TRACE)

    assert judgement == FixJudgement(
        supported=supported, reason="Matches the shift end.", judged_by="judge_proposal.v1"
    )
    assert fake.calls == [judge_messages(FACTS, ROWS, FIX)]


async def test_judge_fix_invalid_output_twice_is_unjudged() -> None:
    fake = FakeLLM('{"supported": "maybe"}', "not json")

    assert await judge_fix(fake, FACTS, ROWS, PROPOSED, TRACE) == UNJUDGED
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    ("error", "logged"),
    [
        (
            LLMUnavailableError("down at 10.0.0.7", status_code=503),
            "llm_unavailable (LLMUnavailableError HTTP 503)",
        ),
        (
            LLMMisconfiguredError("model qwen3:4b not found", status_code=404),
            "llm_misconfigured (LLMMisconfiguredError HTTP 404)",
        ),
    ],
)
async def test_judge_fix_outage_is_unjudged_and_logged_without_ids(
    caplog: pytest.LogCaptureFixture, error: Exception, logged: str
) -> None:
    fake = FakeLLM(error, error)

    with caplog.at_level(logging.WARNING):
        judgement = await judge_fix(fake, FACTS, ROWS, PROPOSED, TRACE)

    messages = [record.getMessage() for record in caplog.records]
    assert judgement == UNJUDGED
    assert [m for m in messages if "proposal judge unavailable" in m and logged in m] != []
    assert [m for m in messages if "10.0.0.7" in m or "not found" in m or SOURCE_ID in m] == []


async def test_judge_fix_traces_prompt_name_and_version() -> None:
    fake = MetadataRecordingLLM(json.dumps({"supported": True, "reason": "ok"}))

    await judge_fix(fake, FACTS, ROWS, PROPOSED, TRACE)

    assert fake.metadata == [
        {"company_id": "tenant", "prompt_name": "judge_proposal", "prompt_version": "1"}
    ]


async def test_judge_messages_have_no_row_ids() -> None:
    fake = FakeLLM()

    await judge_fix(fake, FACTS, ROWS, PROPOSED, TRACE)

    sent = "".join(message.content for call in fake.calls for message in call)
    assert len(fake.calls) == 2
    assert SOURCE_ID not in sent
    assert "row_1" in sent
