import json
from collections.abc import Callable

import httpx
import pytest
from langsmith.run_trees import RunTree
from pydantic import ValidationError

from app.config import LlmSettings
from app.llm.provider import (
    MAX_REPLY_CHARS,
    ChatMessage,
    LLMInvalidReplyError,
    LLMMisconfiguredError,
    LLMResponse,
    LLMUnavailableError,
    OllamaProvider,
    ResponseSchema,
    open_ollama,
    record_usage,
)
from tests.fake_llm import FAKE_EXPLANATION, FAKE_MODEL, FakeLLM

BASE_URL = "http://ollama.test/v1"
MODEL = "qwen3:4b"
MESSAGES = [
    ChatMessage(role="system", content="You explain findings."),
    ChatMessage(role="user", content="<data>\n{}\n</data>"),
]
BODY_MARKER = "RAW-BODY-MARKER not json"
REPLY_SCHEMA = ResponseSchema(
    name="Reply",
    schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    },
)

Handler = Callable[[httpx.Request], httpx.Response]


def _completion(content: str | None = "Reply.", *, usage: bool = True) -> dict[str, object]:
    body: dict[str, object] = {
        "model": "qwen3:4b-served",
        "choices": [{"message": {"role": "assistant", "content": content}}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 12, "completion_tokens": 5}
    return body


def _provider(handler: Handler) -> OllamaProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE_URL)
    return OllamaProvider(client, MODEL)


async def test_ollama_provider_posts_openai_json_schema_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_completion())

    await _provider(handler).complete(MESSAGES, REPLY_SCHEMA)

    request = seen[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert request.url.path == "/v1/chat/completions"
    assert body == {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You explain findings."},
            {"role": "user", "content": "<data>\n{}\n</data>"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "Reply",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
        "temperature": 0,
        "reasoning_effort": "none",
        "max_tokens": 512,
        "stream": False,
    }


async def test_ollama_provider_returns_text_model_and_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion('{"text": "Hi."}'))

    response = await _provider(handler).complete(MESSAGES, REPLY_SCHEMA)

    assert response == LLMResponse(
        text='{"text": "Hi."}', model="qwen3:4b-served", input_tokens=12, output_tokens=5
    )


async def test_ollama_provider_missing_usage_counts_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(usage=False))

    response = await _provider(handler).complete(MESSAGES, REPLY_SCHEMA)

    assert (response.input_tokens, response.output_tokens) == (0, 0)


def _http_500(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="boom")


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def _not_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="not json")


def _empty_choices(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"model": MODEL, "choices": []})


def _null_content(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_completion(None))


def _http_429(request: httpx.Request) -> httpx.Response:
    return httpx.Response(429, text=BODY_MARKER)


def _http_404(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, text=BODY_MARKER)


def _http_400(request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, text=BODY_MARKER)


def _redirect(request: httpx.Request) -> httpx.Response:
    return httpx.Response(307, headers={"location": "http://elsewhere.test/"}, text=BODY_MARKER)


def _model_reply(model: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**_completion(), "model": model})

    return handler


def _reply_content(content: str) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content))

    return handler


@pytest.mark.parametrize(
    "handler",
    [_http_500, _http_429, _timeout, _connect_error, _not_json, _empty_choices, _null_content],
    ids=[
        "http_500",
        "http_429",
        "timeout",
        "connect_error",
        "not_json",
        "empty_choices",
        "null_content",
    ],
)
async def test_ollama_provider_failure_raises_unavailable(handler: Handler) -> None:
    with pytest.raises(LLMUnavailableError) as caught:
        await _provider(handler).complete(MESSAGES, REPLY_SCHEMA)

    assert not isinstance(caught.value, LLMMisconfiguredError)


@pytest.mark.parametrize(
    "handler",
    [_http_404, _http_400, _redirect],
    ids=["http_404", "http_400", "redirect"],
)
async def test_ollama_provider_permanent_failure_raises_misconfigured(handler: Handler) -> None:
    with pytest.raises(LLMMisconfiguredError) as caught:
        await _provider(handler).complete(MESSAGES, REPLY_SCHEMA)

    assert isinstance(caught.value, LLMUnavailableError)
    assert "RAW-BODY-MARKER" not in str(caught.value)
    assert "RAW-BODY-MARKER" not in repr(caught.value)


def _client_error(status: int, body: object) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"message": "invalid JSON schema in format", "type": "invalid_request_error"}},
        {"error": "invalid JSON schema in format"},
    ],
    ids=["openai_shape", "ollama_shape"],
)
async def test_ollama_provider_4xx_names_server_error_reason(body: object) -> None:
    with pytest.raises(LLMMisconfiguredError) as caught:
        await _provider(_client_error(400, body)).complete(MESSAGES, REPLY_SCHEMA)

    assert str(caught.value) == (
        "Ollama rejected the request (HTTP 400): invalid JSON schema in format"
    )


async def test_ollama_provider_4xx_reason_is_capped_and_cleaned() -> None:
    message = "bad\x00 sch\x1bema\n" + "x" * 300 + "TAIL-MARKER"
    body = {"error": {"message": message}}

    with pytest.raises(LLMMisconfiguredError) as caught:
        await _provider(_client_error(404, body)).complete(MESSAGES, REPLY_SCHEMA)

    reason = str(caught.value).removeprefix("Ollama rejected the request (HTTP 404): ")
    assert reason.startswith("bad schema")
    assert len(reason) == 200
    assert "TAIL-MARKER" not in str(caught.value)
    assert not any(ord(char) < 32 or ord(char) == 127 for char in str(caught.value))


async def test_ollama_provider_4xx_huge_reason_is_capped() -> None:
    body = {"error": {"message": "y" * 5_000_000 + "TAIL-MARKER"}}

    with pytest.raises(LLMMisconfiguredError) as caught:
        await _provider(_client_error(400, body)).complete(MESSAGES, REPLY_SCHEMA)

    assert str(caught.value) == "Ollama rejected the request (HTTP 400): " + "y" * 200


@pytest.mark.parametrize(
    "body",
    [{"error": 5}, {"error": {"code": 1}}, {"detail": "x"}, ["error"], {"error": "\x00\n"}],
    ids=["number", "no_message", "other_key", "list", "only_control_chars"],
)
async def test_ollama_provider_4xx_unparseable_reason_keeps_plain_message(body: object) -> None:
    with pytest.raises(LLMMisconfiguredError) as caught:
        await _provider(_client_error(400, body)).complete(MESSAGES, REPLY_SCHEMA)

    assert str(caught.value) == "Ollama rejected the request (HTTP 400)."


@pytest.mark.parametrize("status", [500, 429], ids=["http_500", "http_429"])
async def test_ollama_provider_retryable_failure_never_names_reason(status: int) -> None:
    body = {"error": {"message": "RAW-BODY-MARKER"}}

    with pytest.raises(LLMUnavailableError) as caught:
        await _provider(_client_error(status, body)).complete(MESSAGES, REPLY_SCHEMA)

    assert str(caught.value) == f"Ollama request failed (HTTP {status})."


async def test_ollama_provider_error_never_echoes_body() -> None:
    def ok_but_not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=BODY_MARKER)

    def server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=BODY_MARKER)

    with pytest.raises(LLMUnavailableError) as not_json:
        await _provider(ok_but_not_json).complete(MESSAGES, REPLY_SCHEMA)
    with pytest.raises(LLMUnavailableError) as failed:
        await _provider(server_error).complete(MESSAGES, REPLY_SCHEMA)

    for error in (not_json.value, failed.value):
        assert "RAW-BODY-MARKER" not in str(error)
        assert "RAW-BODY-MARKER" not in repr(error)
    assert not_json.value.__cause__ is None
    assert not_json.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "model",
    ["", "m" * 201, "qwen3\x00" + BODY_MARKER],
    ids=["empty", "too_long", "nul"],
)
async def test_ollama_provider_rejects_bad_served_model_name(model: str) -> None:
    with pytest.raises(LLMUnavailableError, match="not a valid chat completion") as caught:
        await _provider(_model_reply(model)).complete(MESSAGES, REPLY_SCHEMA)

    assert "RAW-BODY-MARKER" not in str(caught.value)
    assert "RAW-BODY-MARKER" not in repr(caught.value)


async def test_ollama_provider_accepts_served_model_name_at_max_length() -> None:
    response = await _provider(_model_reply("m" * 200)).complete(MESSAGES, REPLY_SCHEMA)

    assert response.model == "m" * 200


@pytest.mark.parametrize(
    "base_url",
    [
        "http://xn--/v1",
        "http://host:abc/v1",
        "http://host:99999/v1",
        "http://\x00host/v1",
        "http://a b/v1",
        "http://[::1]:8080/v1",
        "https://ollama.internal/v1",
        "http://host:0/v1",
    ],
)
async def test_every_accepted_base_url_fails_only_with_llm_errors(base_url: str) -> None:
    try:
        settings = LlmSettings(llm_base_url=base_url)
    except ValidationError:
        return
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_connect_error), base_url=settings.llm_base_url
    )

    with pytest.raises(LLMUnavailableError):
        await OllamaProvider(client, MODEL).complete(MESSAGES, REPLY_SCHEMA)


def test_record_usage_sets_token_counts() -> None:
    run_tree = RunTree(name="ollama.chat", run_type="llm")
    response = LLMResponse(text="{}", model="qwen3:4b-served", input_tokens=12, output_tokens=5)

    record_usage(run_tree, response)

    metadata = run_tree.metadata
    assert metadata["usage_metadata"] == {
        "input_tokens": 12,
        "output_tokens": 5,
        "total_tokens": 17,
    }
    assert metadata["ls_model_name"] == "qwen3:4b-served"


async def test_open_ollama_builds_provider_from_settings() -> None:
    settings = LlmSettings(llm_model="qwen3:8b", llm_base_url=BASE_URL, llm_timeout_seconds=77)

    async with open_ollama(settings) as provider:
        client = provider._client
        assert provider.model == "qwen3:8b"
        assert client.base_url == httpx.URL(BASE_URL + "/")
        assert client.timeout == httpx.Timeout(77)
    assert client.is_closed


async def test_fake_llm_replays_scripted_replies_then_default() -> None:
    fake = FakeLLM('{"text": "First."}')

    first = await fake.complete(MESSAGES, REPLY_SCHEMA)
    second = await fake.complete(MESSAGES, REPLY_SCHEMA)

    assert first == LLMResponse(
        text='{"text": "First."}', model=FAKE_MODEL, input_tokens=0, output_tokens=0
    )
    assert json.loads(second.text) == {"text": FAKE_EXPLANATION}


async def test_fake_llm_raises_scripted_exception() -> None:
    fake = FakeLLM(LLMUnavailableError("down"), '{"text": "Later."}')

    with pytest.raises(LLMUnavailableError, match="down"):
        await fake.complete(MESSAGES, REPLY_SCHEMA)
    assert (await fake.complete(MESSAGES, REPLY_SCHEMA)).text == '{"text": "Later."}'


async def test_fake_llm_records_calls() -> None:
    fake = FakeLLM()

    await fake.complete(MESSAGES, REPLY_SCHEMA)
    await fake.complete(MESSAGES[:1], REPLY_SCHEMA)

    assert fake.calls == [MESSAGES, MESSAGES[:1]]
    assert fake.schemas == [REPLY_SCHEMA, REPLY_SCHEMA]


async def test_ollama_provider_refuses_oversized_reply_as_invalid() -> None:
    oversized = BODY_MARKER + "<think>" * (MAX_REPLY_CHARS // len("<think>"))

    with pytest.raises(LLMInvalidReplyError) as caught:
        await _provider(_reply_content(oversized)).complete(MESSAGES, REPLY_SCHEMA)

    assert not isinstance(caught.value, LLMUnavailableError)
    assert "RAW-BODY-MARKER" not in str(caught.value)
    assert "RAW-BODY-MARKER" not in repr(caught.value)


async def test_ollama_provider_accepts_reply_at_max_length() -> None:
    response = await _provider(_reply_content("x" * MAX_REPLY_CHARS)).complete(
        MESSAGES, REPLY_SCHEMA
    )

    assert len(response.text) == MAX_REPLY_CHARS


async def test_open_ollama_ignores_proxy_environment() -> None:
    async with open_ollama(LlmSettings(llm_base_url=BASE_URL)) as provider:
        assert provider._client.trust_env is False
