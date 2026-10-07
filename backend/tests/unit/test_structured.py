import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.llm.provider import (
    ChatMessage,
    LLMInvalidReplyError,
    LLMMisconfiguredError,
    LLMUnavailableError,
    ResponseSchema,
)
from app.llm.structured import LLMOutputError, StructuredReply, complete_structured
from tests.fake_llm import FAKE_MODEL, FakeLLM

MESSAGES = [ChatMessage(role="user", content="Explain.")]
RAW_REPLY = "RAW-REPLY-MARKER {"


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class _Tag(BaseModel):
    label: str = Field(min_length=1, max_length=20, pattern=r"^[a-z]+$")


class _ConstrainedReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=5)
    pattern: str = Field(default="keep-me", max_length=10)
    tags: list[_Tag] = Field(min_length=1, max_length=3)
    note: str | None = Field(default=None, max_length=4)


async def test_complete_structured_returns_validated_value_and_model() -> None:
    fake = FakeLLM('{"text": "Valid."}')

    reply = await complete_structured(fake, MESSAGES, _Reply)

    assert reply == StructuredReply(value=_Reply(text="Valid."), model=FAKE_MODEL)
    assert fake.calls == [MESSAGES]


async def test_complete_structured_asks_for_the_model_schema_every_attempt() -> None:
    fake = FakeLLM("not json", '{"text": "Second."}')

    await complete_structured(fake, MESSAGES, _Reply)

    expected = ResponseSchema(
        name="_Reply",
        schema={
            "additionalProperties": False,
            "properties": {"text": {"title": "Text", "type": "string"}},
            "required": ["text"],
            "title": "_Reply",
            "type": "object",
        },
    )
    assert fake.schemas == [expected, expected]


async def test_complete_structured_still_validates_reply_against_model() -> None:
    fake = FakeLLM('{"text": "x", "extra": 1}', '{"text": 5}')

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _Reply)

    assert caught.value.reason == "invalid_output"


async def test_complete_structured_strips_think_block() -> None:
    fake = FakeLLM('<think>\nmull {"text": "no"}\n</think>\n\n  {"text": "After."}  ')

    reply = await complete_structured(fake, MESSAGES, _Reply)

    assert reply.value == _Reply(text="After.")


async def test_complete_structured_retries_once_then_succeeds() -> None:
    fake = FakeLLM("not json", '{"text": "Second."}')

    reply = await complete_structured(fake, MESSAGES, _Reply)

    assert reply.value == _Reply(text="Second.")
    assert len(fake.calls) == 2


async def test_complete_structured_raises_output_error_after_two_failures() -> None:
    fake = FakeLLM("not json", '{"text": "x", "extra": 1}', '{"text": "never asked"}')

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _Reply)

    assert caught.value.reason == "invalid_output"
    assert len(fake.calls) == 2


async def test_complete_structured_retries_after_unavailable() -> None:
    fake = FakeLLM(LLMUnavailableError("down"), '{"text": "Back."}')

    reply = await complete_structured(fake, MESSAGES, _Reply)

    assert reply.value == _Reply(text="Back.")
    assert len(fake.calls) == 2


async def test_complete_structured_raises_unavailable_reason_when_provider_down() -> None:
    fake = FakeLLM(LLMUnavailableError("down"), LLMUnavailableError("down"))

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _Reply)

    assert caught.value.reason == "llm_unavailable"


async def test_complete_structured_reason_is_last_attempt_failure() -> None:
    fake = FakeLLM(LLMUnavailableError("down"), "not json")

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _Reply)

    assert caught.value.reason == "invalid_output"


async def test_complete_structured_error_never_echoes_raw_reply() -> None:
    fake = FakeLLM(RAW_REPLY, RAW_REPLY)

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _Reply)

    error = caught.value
    assert "RAW-REPLY-MARKER" not in str(error)
    assert "RAW-REPLY-MARKER" not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


async def test_complete_structured_does_not_catch_other_errors() -> None:
    fake = FakeLLM(RuntimeError("bug"), '{"text": "never asked"}')

    with pytest.raises(RuntimeError, match="bug"):
        await complete_structured(fake, MESSAGES, _Reply)

    assert len(fake.calls) == 1


async def test_complete_structured_does_not_retry_misconfigured_provider() -> None:
    fake = FakeLLM(LLMMisconfiguredError("404"), '{"text": "never asked"}')

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _Reply)

    assert caught.value.reason == "llm_misconfigured"
    assert len(fake.calls) == 1
    assert "2 attempts" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


async def test_complete_structured_stops_on_misconfigured_after_invalid_reply() -> None:
    fake = FakeLLM("not json", LLMMisconfiguredError("404"))

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _Reply)

    assert caught.value.reason == "llm_misconfigured"
    assert len(fake.calls) == 2


async def test_complete_structured_invalid_reply_retries_then_reports_invalid_output() -> None:
    fake = FakeLLM(LLMInvalidReplyError("too long"), LLMInvalidReplyError("too long"))

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _Reply)

    assert caught.value.reason == "invalid_output"
    assert len(fake.calls) == 2


async def test_wire_schema_carries_no_provider_enforced_length_keywords() -> None:
    fake = FakeLLM('{"text": "ok", "tags": [{"label": "a"}]}')

    await complete_structured(fake, MESSAGES, _ConstrainedReply)

    assert fake.schemas[0].schema == {
        "$defs": {
            "_Tag": {
                "properties": {"label": {"title": "Label", "type": "string"}},
                "required": ["label"],
                "title": "_Tag",
                "type": "object",
            }
        },
        "additionalProperties": False,
        "properties": {
            "text": {"title": "Text", "type": "string"},
            "pattern": {"default": "keep-me", "title": "Pattern", "type": "string"},
            "tags": {"items": {"$ref": "#/$defs/_Tag"}, "title": "Tags", "type": "array"},
            "note": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "default": None,
                "title": "Note",
            },
        },
        "required": ["text", "tags"],
        "title": "_ConstrainedReply",
        "type": "object",
    }


async def test_pydantic_still_enforces_lengths_the_wire_schema_dropped() -> None:
    too_long = '{"text": "toolong", "tags": [{"label": "a"}]}'
    bad_pattern = '{"text": "ok", "tags": [{"label": "A1"}]}'
    fake = FakeLLM(too_long, bad_pattern)

    with pytest.raises(LLMOutputError) as caught:
        await complete_structured(fake, MESSAGES, _ConstrainedReply)

    assert caught.value.reason == "invalid_output"
    assert len(fake.calls) == 2
