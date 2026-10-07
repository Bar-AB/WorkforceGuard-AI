import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, JsonValue, ValidationError

from app.errors import DomainError
from app.llm.provider import (
    ChatMessage,
    LLMInvalidReplyError,
    LLMMisconfiguredError,
    LLMProvider,
    LLMUnavailableError,
    ResponseSchema,
)

MAX_ATTEMPTS: Final = 2

FallbackReason = Literal["llm_unavailable", "llm_misconfigured", "invalid_output"]

_THINK_BLOCK: Final = re.compile(r"<think>.*?</think>", re.DOTALL)
_PROVIDER_ENFORCED_KEYWORDS: Final = frozenset(
    {"maxLength", "minLength", "maxItems", "minItems", "pattern"}
)
_SUBSCHEMA_MAPS: Final = frozenset({"properties", "$defs"})
_DATA_KEYWORDS: Final = frozenset({"default", "const", "enum", "examples"})


class LLMOutputError(DomainError):
    def __init__(self, reason: FallbackReason) -> None:
        super().__init__(f"No valid LLM reply ({reason}).")
        self.reason: FallbackReason = reason


@dataclass(frozen=True)
class _Failure:
    reason: FallbackReason
    retryable: bool


@dataclass(frozen=True)
class StructuredReply[T: BaseModel]:
    value: T
    model: str


def _strip_think(text: str) -> str:
    return _THINK_BLOCK.sub("", text).strip()


def _wire_object(node: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        key: _wire_member(key, value)
        for key, value in node.items()
        if key not in _PROVIDER_ENFORCED_KEYWORDS
    }


def _wire_member(key: str, value: JsonValue) -> JsonValue:
    if key in _DATA_KEYWORDS:
        return value
    if key in _SUBSCHEMA_MAPS and isinstance(value, dict):
        return {name: _wire_node(subschema) for name, subschema in value.items()}
    return _wire_node(value)


def _wire_node(node: JsonValue) -> JsonValue:
    if isinstance(node, list):
        return [_wire_node(item) for item in node]
    if isinstance(node, dict):
        return _wire_object(node)
    return node


def _response_schema(schema: type[BaseModel]) -> ResponseSchema:
    return ResponseSchema(name=schema.__name__, schema=_wire_object(schema.model_json_schema()))


async def _attempt[T: BaseModel](
    provider: LLMProvider,
    messages: Sequence[ChatMessage],
    schema: type[T],
    wire_schema: ResponseSchema,
) -> StructuredReply[T] | _Failure:
    try:
        response = await provider.complete(messages, wire_schema)
    except LLMMisconfiguredError:
        return _Failure("llm_misconfigured", retryable=False)
    except LLMUnavailableError:
        return _Failure("llm_unavailable", retryable=True)
    except LLMInvalidReplyError:
        return _Failure("invalid_output", retryable=True)
    try:
        value = schema.model_validate_json(_strip_think(response.text))
    except ValidationError:
        return _Failure("invalid_output", retryable=True)
    return StructuredReply(value=value, model=response.model)


async def complete_structured[T: BaseModel](
    provider: LLMProvider, messages: Sequence[ChatMessage], schema: type[T]
) -> StructuredReply[T]:
    wire_schema = _response_schema(schema)
    last_reason: FallbackReason = "invalid_output"
    for _ in range(MAX_ATTEMPTS):
        outcome = await _attempt(provider, messages, schema, wire_schema)
        if isinstance(outcome, StructuredReply):
            return outcome
        last_reason = outcome.reason
        if not outcome.retryable:
            break
    raise LLMOutputError(last_reason)
