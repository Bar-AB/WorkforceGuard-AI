import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, JsonValue, ValidationError

from app.config import LLM_MAX_ATTEMPTS
from app.errors import DomainError
from app.llm.provider import (
    ChatMessage,
    LLMInvalidReplyError,
    LLMMisconfiguredError,
    LLMProvider,
    LLMUnavailableError,
    ResponseSchema,
)

FallbackReason = Literal["llm_unavailable", "llm_misconfigured", "invalid_output"]

_THINK_BLOCK: Final = re.compile(r"<think>.*?</think>", re.DOTALL)
_PROVIDER_ENFORCED_KEYWORDS: Final = frozenset(
    {"maxLength", "minLength", "maxItems", "minItems", "pattern"}
)
_SUBSCHEMA_MAPS: Final = frozenset({"properties", "$defs"})
_DATA_KEYWORDS: Final = frozenset({"default", "const", "enum", "examples"})


class LLMOutputError(DomainError):
    def __init__(self, reason: FallbackReason, detail: str) -> None:
        super().__init__(f"No valid LLM reply ({reason}).")
        self.reason: FallbackReason = reason
        self.detail = detail


@dataclass(frozen=True)
class _Failure:
    reason: FallbackReason
    retryable: bool
    detail: str


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


def _failure_detail(error: Exception) -> str:
    parts = [type(error).__name__]
    if isinstance(error, LLMUnavailableError) and error.status_code is not None:
        parts.append(f"HTTP {error.status_code}")
    if error.__cause__ is not None:
        parts.append(f"from {type(error.__cause__).__name__}")
    return " ".join(parts)


async def _attempt[T: BaseModel](
    provider: LLMProvider,
    messages: Sequence[ChatMessage],
    schema: type[T],
    wire_schema: ResponseSchema,
) -> StructuredReply[T] | _Failure:
    try:
        response = await provider.complete(messages, wire_schema)
    except LLMMisconfiguredError as error:
        return _Failure("llm_misconfigured", retryable=False, detail=_failure_detail(error))
    except LLMUnavailableError as error:
        return _Failure("llm_unavailable", retryable=True, detail=_failure_detail(error))
    except LLMInvalidReplyError as error:
        return _Failure("invalid_output", retryable=True, detail=_failure_detail(error))
    try:
        value = schema.model_validate_json(_strip_think(response.text))
    except ValidationError as error:
        return _Failure("invalid_output", retryable=True, detail=_failure_detail(error))
    return StructuredReply(value=value, model=response.model)


async def complete_structured[T: BaseModel](
    provider: LLMProvider, messages: Sequence[ChatMessage], schema: type[T]
) -> StructuredReply[T]:
    wire_schema = _response_schema(schema)
    failure = _Failure("invalid_output", retryable=True, detail="no attempt")
    for _ in range(LLM_MAX_ATTEMPTS):
        outcome = await _attempt(provider, messages, schema, wire_schema)
        if isinstance(outcome, StructuredReply):
            return outcome
        failure = outcome
        if not failure.retryable:
            break
    raise LLMOutputError(failure.reason, failure.detail)
