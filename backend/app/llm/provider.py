from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final, Literal, Protocol

import httpx
from langsmith import get_current_run_tree, traceable
from langsmith.run_trees import RunTree
from pydantic import BaseModel, Field, JsonValue, ValidationError

from app.config import LlmSettings
from app.errors import DomainError


class ChatMessage(BaseModel):
    role: Literal["system", "user"]
    content: str


class LLMResponse(BaseModel):
    text: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ResponseSchema:
    name: str
    schema: Mapping[str, JsonValue]


class LLMUnavailableError(DomainError):
    pass


class LLMMisconfiguredError(LLMUnavailableError):
    pass


class LLMInvalidReplyError(DomainError):
    pass


class LLMProvider(Protocol):
    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse: ...


_TRANSIENT_CLIENT_STATUSES: Final = frozenset({408, 429})
NO_NUL_PATTERN: Final = r"^[^\x00]*$"
MAX_REPLY_CHARS: Final = 32_000
MAX_REPLY_TOKENS: Final = 512
MAX_ERROR_REASON_CHARS: Final = 200


class _Message(BaseModel):
    content: str


class _Choice(BaseModel):
    message: _Message


class _Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class _ErrorDetail(BaseModel):
    message: str


class _ErrorBody(BaseModel):
    error: str | _ErrorDetail


class _ChatCompletion(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=NO_NUL_PATTERN)
    choices: list[_Choice] = Field(min_length=1)
    usage: _Usage = Field(default_factory=_Usage)


def _trace_inputs(inputs: Mapping[str, object]) -> dict[str, object]:
    return {"messages": inputs.get("messages")}


def record_usage(run_tree: RunTree | None, response: LLMResponse) -> None:
    if run_tree is None:
        return
    run_tree.set(
        metadata={"ls_model_name": response.model},
        usage_metadata={
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "total_tokens": response.input_tokens + response.output_tokens,
        },
    )


def _server_error_reason(body: bytes) -> str | None:
    try:
        error = _ErrorBody.model_validate_json(body).error
    except ValidationError:
        return None
    message = error if isinstance(error, str) else error.message
    head = message[: MAX_ERROR_REASON_CHARS * 4]
    printable = "".join(char for char in head if char.isprintable())
    return printable.strip()[:MAX_ERROR_REASON_CHARS] or None


def _rejected(response: httpx.Response) -> LLMMisconfiguredError:
    status = response.status_code
    reason = None if response.is_redirect else _server_error_reason(response.content)
    if reason is None:
        return LLMMisconfiguredError(f"Ollama rejected the request (HTTP {status}).")
    return LLMMisconfiguredError(f"Ollama rejected the request (HTTP {status}): {reason}")


def _raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    status = response.status_code
    if response.is_redirect or (
        response.is_client_error and status not in _TRANSIENT_CLIENT_STATUSES
    ):
        raise _rejected(response)
    raise LLMUnavailableError(f"Ollama request failed (HTTP {status}).")


def _parse_completion(body: bytes) -> _ChatCompletion:
    try:
        return _ChatCompletion.model_validate_json(body)
    except ValidationError:
        raise LLMUnavailableError("Ollama reply is not a valid chat completion.") from None


def _reply_text(completion: _ChatCompletion) -> str:
    text = completion.choices[0].message.content
    if len(text) > MAX_REPLY_CHARS:
        raise LLMInvalidReplyError("Ollama reply is too long.")
    return text


def _json_schema_format(schema: ResponseSchema) -> dict[str, JsonValue]:
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.name, "strict": True, "schema": dict(schema.schema)},
    }


class OllamaProvider:
    def __init__(self, client: httpx.AsyncClient, model: str) -> None:
        self._client = client
        self.model = model

    @traceable(
        run_type="llm",
        name="ollama.chat",
        metadata={"ls_provider": "ollama"},
        process_inputs=_trace_inputs,
    )
    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        body = await self._post(messages, schema)
        completion = _parse_completion(body)
        response = LLMResponse(
            text=_reply_text(completion),
            model=completion.model,
            input_tokens=completion.usage.prompt_tokens,
            output_tokens=completion.usage.completion_tokens,
        )
        record_usage(get_current_run_tree(), response)
        return response

    async def _post(self, messages: Sequence[ChatMessage], schema: ResponseSchema) -> bytes:
        payload = {
            "model": self.model,
            "messages": [message.model_dump() for message in messages],
            "response_format": _json_schema_format(schema),
            "temperature": 0,
            "reasoning_effort": "none",
            "max_tokens": MAX_REPLY_TOKENS,
            "stream": False,
        }
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as error:
            raise LLMUnavailableError("Ollama request failed.") from error
        _raise_for_status(response)
        return response.content


@asynccontextmanager
async def open_ollama(settings: LlmSettings) -> AsyncIterator[OllamaProvider]:
    async with httpx.AsyncClient(
        base_url=settings.llm_base_url, timeout=settings.llm_timeout_seconds, trust_env=False
    ) as client:
        yield OllamaProvider(client, settings.llm_model)
