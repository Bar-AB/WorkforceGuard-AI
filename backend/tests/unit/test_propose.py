import json
import logging
from datetime import date
from typing import Final

import pytest

from app.llm.explain import FindingFacts
from app.llm.propose import (
    INVALID_FIX_REASON,
    NO_CHANGE_REASON,
    PROPOSE_PROMPT,
    CandidateRow,
    NoFix,
    ProposedFix,
    proposal_payload,
    propose_fix,
)
from app.llm.provider import LLMMisconfiguredError, LLMUnavailableError
from tests.fake_llm import FAKE_MODEL, FakeLLM

FACTS: Final = FindingFacts(
    rule_id="overtime_breach",
    severity="high",
    occurred_on=date(2026, 1, 6),
    summary="Worked 13 hours on 2026-01-06.",
    evidence={"limit": "daily", "hours": "13", "max_hours": "12", "source_table": "shifts"},
)
ROWS: Final = (
    CandidateRow(
        alias="row_1",
        target_table="shifts",
        fields={"starts_at": "2026-01-06T08:00:00+00:00", "ends_at": "2026-01-06T21:00:00+00:00"},
    ),
    CandidateRow(
        alias="row_2",
        target_table="attendance_events",
        fields={
            "event_type": "clock_out",
            "occurred_at": "2026-01-06T21:00:00+00:00",
            "source": "terminal",
        },
    ),
)
TRACE: Final = {"company_id": "tenant"}
FIX_REASON: Final = "The clock-out is one hour after the last door exit."


def _reply(
    action: str = "fix",
    target: str = "row_1",
    column: str = "ends_at",
    value: str = "2026-01-06T20:00:00+00:00",
    reason: str = FIX_REASON,
) -> str:
    return json.dumps(
        {"action": action, "target": target, "column": column, "value": value, "reason": reason}
    )


async def test_propose_fix_valid_reply_returns_fix() -> None:
    fake = FakeLLM(_reply())

    result = await propose_fix(fake, FACTS, ROWS, TRACE)

    assert result == ProposedFix(
        alias="row_1",
        target_table="shifts",
        patch={"ends_at": "2026-01-06T20:00:00+00:00"},
        reason=FIX_REASON,
        proposed_by=f"llm:{FAKE_MODEL}:propose_correction.v1",
    )


async def test_propose_fix_attendance_event_type_returns_fix() -> None:
    fake = FakeLLM(_reply(target="row_2", column="event_type", value="clock_in"))

    result = await propose_fix(fake, FACTS, ROWS, TRACE)

    assert isinstance(result, ProposedFix)
    assert (result.target_table, result.patch) == ("attendance_events", {"event_type": "clock_in"})


async def test_propose_fix_no_fix_reply_returns_reason() -> None:
    fake = FakeLLM(_reply(action="no_fix", target="", column="", value="", reason="Real work."))

    result = await propose_fix(fake, FACTS, ROWS, TRACE)

    assert result == NoFix(reason="Real work.", fallback_reason=None)


async def test_propose_fix_unknown_alias_falls_back() -> None:
    result = await propose_fix(FakeLLM(_reply(target="row_3")), FACTS, ROWS, TRACE)

    assert result == NoFix(reason=INVALID_FIX_REASON, fallback_reason="invalid_output")


@pytest.mark.parametrize(
    ("target", "column", "value"),
    [
        ("row_1", "company_id", "2026-01-06T20:00:00+00:00"),
        ("row_2", "source", "manual"),
        ("row_1", "event_type", "clock_in"),
    ],
    ids=["locked-column", "not-proposer-column", "column-of-other-table"],
)
async def test_propose_fix_disallowed_column_falls_back(
    target: str, column: str, value: str
) -> None:
    reply = _reply(target=target, column=column, value=value)

    result = await propose_fix(FakeLLM(reply), FACTS, ROWS, TRACE)

    assert result == NoFix(reason=INVALID_FIX_REASON, fallback_reason="invalid_output")


@pytest.mark.parametrize("value", ["yesterday", "2026-01-06T20:00:00", "2026-13-01T20:00:00Z"])
async def test_propose_fix_invalid_timestamp_falls_back(value: str) -> None:
    result = await propose_fix(FakeLLM(_reply(value=value)), FACTS, ROWS, TRACE)

    assert result == NoFix(reason=INVALID_FIX_REASON, fallback_reason="invalid_output")


async def test_propose_fix_invalid_event_type_falls_back() -> None:
    reply = _reply(target="row_2", column="event_type", value="lunch")

    result = await propose_fix(FakeLLM(reply), FACTS, ROWS, TRACE)

    assert result == NoFix(reason=INVALID_FIX_REASON, fallback_reason="invalid_output")


async def test_propose_fix_invalid_json_twice_falls_back() -> None:
    fake = FakeLLM("not json", '{"action": "fix"}')

    result = await propose_fix(fake, FACTS, ROWS, TRACE)

    assert result == NoFix(reason=INVALID_FIX_REASON, fallback_reason="invalid_output")
    assert len(fake.calls) == 2


async def test_propose_fix_fix_with_empty_value_falls_back() -> None:
    result = await propose_fix(FakeLLM(_reply(value="  ")), FACTS, ROWS, TRACE)

    assert result == NoFix(reason=INVALID_FIX_REASON, fallback_reason="invalid_output")


async def test_propose_fix_outage_raises_unavailable(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeLLM(
        LLMUnavailableError("down at 10.0.0.7", status_code=503),
        LLMUnavailableError("down at 10.0.0.7", status_code=503),
    )

    with caplog.at_level(logging.WARNING), pytest.raises(LLMUnavailableError) as raised:
        await propose_fix(fake, FACTS, ROWS, TRACE)

    messages = [record.getMessage() for record in caplog.records]
    assert str(raised.value) == "The AI model is unavailable. Try again later."
    assert [m for m in messages if "llm_unavailable (LLMUnavailableError HTTP 503)" in m] != []
    assert [m for m in messages if "10.0.0.7" in m] == []


async def test_propose_fix_misconfigured_raises_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeLLM(LLMMisconfiguredError("model qwen3:4b not found", status_code=404))

    with caplog.at_level(logging.WARNING), pytest.raises(LLMUnavailableError) as raised:
        await propose_fix(fake, FACTS, ROWS, TRACE)

    messages = [record.getMessage() for record in caplog.records]
    assert str(raised.value) == "The AI model is not configured correctly."
    assert len(fake.calls) == 1
    assert [m for m in messages if "llm_misconfigured (LLMMisconfiguredError HTTP 404)" in m] != []
    assert [m for m in messages if "not found" in m] == []


async def test_propose_fix_without_rows_skips_llm() -> None:
    fake = FakeLLM()

    result = await propose_fix(fake, FACTS, (), TRACE)

    assert result == NoFix(
        reason="No shift or attendance rows were found for that day.", fallback_reason=None
    )
    assert fake.calls == []


async def test_propose_fix_wraps_payload_in_data_block() -> None:
    fake = FakeLLM(_reply())

    await propose_fix(fake, FACTS, ROWS, TRACE)

    [[system, user]] = fake.calls
    assert (system.role, system.content) == ("system", PROPOSE_PROMPT.text)
    assert user.role == "user"
    assert user.content.startswith("<data>\n")
    assert user.content.endswith("\n</data>")
    assert '"alias": "row_1"' in user.content


async def test_propose_fix_requests_flat_reply_schema() -> None:
    fake = FakeLLM(_reply())

    await propose_fix(fake, FACTS, ROWS, TRACE)

    [schema] = fake.schemas
    assert schema.schema["required"] == ["action", "target", "column", "value", "reason"]


def test_proposal_payload_has_aliases_not_ids() -> None:
    payload = proposal_payload(FACTS, ROWS[:1])

    assert payload == {
        "finding": {
            "rule_id": "overtime_breach",
            "severity": "high",
            "occurred_on": "2026-01-06",
            "evidence": {"limit": "daily", "hours": "13", "max_hours": "12"},
        },
        "rows": [
            {
                "alias": "row_1",
                "table": "shifts",
                "fields": {
                    "starts_at": "2026-01-06T08:00:00+00:00",
                    "ends_at": "2026-01-06T21:00:00+00:00",
                },
            }
        ],
    }


async def test_propose_fix_proposed_by_names_model_and_prompt() -> None:
    result = await propose_fix(FakeLLM(_reply()), FACTS, ROWS, TRACE)

    assert isinstance(result, ProposedFix)
    assert result.proposed_by == "llm:fake-llm:propose_correction.v1"


def test_propose_prompt_v1_loads() -> None:
    text = PROPOSE_PROMPT.text

    assert PROPOSE_PROMPT.label == "propose_correction.v1"
    assert "<data>" in text
    assert '"action"' in text
    assert "no_fix" in text
    assert "/no_think" not in text


@pytest.mark.parametrize(
    "value", ["2026-01-06T21:00:00+00:00", "2026-01-06T21:00:00Z", "2026-01-06T23:00:00+02:00"]
)
async def test_propose_fix_value_equal_to_current_is_no_fix(value: str) -> None:
    result = await propose_fix(FakeLLM(_reply(value=value)), FACTS, ROWS, TRACE)

    assert result == NoFix(reason=NO_CHANGE_REASON, fallback_reason="no_change")


async def test_propose_fix_event_type_equal_to_current_is_no_fix() -> None:
    reply = _reply(target="row_2", column="event_type", value="clock_out")

    result = await propose_fix(FakeLLM(reply), FACTS, ROWS, TRACE)

    assert result == NoFix(reason=NO_CHANGE_REASON, fallback_reason="no_change")


async def test_propose_fix_current_value_out_of_range_counts_as_change() -> None:
    rows = (
        CandidateRow(
            alias="row_1",
            target_table="shifts",
            fields={
                "starts_at": "2100-12-31T20:00:00+00:00",
                "ends_at": "2101-01-01T02:00:00+00:00",
            },
        ),
    )
    reply = _reply(value="2100-12-31T23:00:00+00:00")

    result = await propose_fix(FakeLLM(reply), FACTS, rows, TRACE)

    assert isinstance(result, ProposedFix)
    assert result.patch == {"ends_at": "2100-12-31T23:00:00+00:00"}
