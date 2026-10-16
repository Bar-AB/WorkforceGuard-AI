from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ApiSettings, LlmSettings, Settings

ENV_VARS = (
    "DATABASE_URL",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "LLM_EXPLAIN_LIMIT",
    "LLM_SUGGEST_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_settings_database_url_is_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/x"


def test_llm_settings_defaults() -> None:
    settings = LlmSettings()

    assert settings.llm_base_url == "http://localhost:11434/v1"
    assert settings.llm_model == "qwen3:4b"
    assert settings.llm_timeout_seconds == 120.0
    assert settings.llm_explain_limit == 10


def test_llm_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen3:8b")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("LLM_EXPLAIN_LIMIT", "4")

    settings = LlmSettings()

    assert settings.llm_base_url == "http://ollama:11434/v1"
    assert settings.llm_model == "qwen3:8b"
    assert settings.llm_timeout_seconds == 90.0
    assert settings.llm_explain_limit == 4


def test_llm_settings_read_from_env_file(isolated_env: Path) -> None:
    (isolated_env / ".env").write_text("LLM_EXPLAIN_LIMIT=3\n", encoding="utf-8")

    assert LlmSettings().llm_explain_limit == 3


def test_llm_settings_reject_negative_explain_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_EXPLAIN_LIMIT", "-1")

    with pytest.raises(ValidationError, match="llm_explain_limit"):
        LlmSettings()


def test_llm_settings_reject_explain_limit_above_upper_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_EXPLAIN_LIMIT", "101")

    with pytest.raises(ValidationError, match="llm_explain_limit"):
        LlmSettings()


def test_llm_settings_accept_explain_limit_at_upper_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_EXPLAIN_LIMIT", "100")

    assert LlmSettings().llm_explain_limit == 100


def test_llm_settings_reject_non_positive_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValidationError, match="llm_timeout_seconds"):
        LlmSettings()


def test_llm_settings_need_no_database_url() -> None:
    settings = LlmSettings()

    assert not hasattr(settings, "database_url")


@pytest.mark.parametrize("timeout", ["inf", "-inf", "nan", "601"])
def test_llm_settings_reject_unbounded_timeout(
    timeout: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", timeout)

    with pytest.raises(ValidationError, match="llm_timeout_seconds"):
        LlmSettings()


def test_llm_settings_accept_timeout_at_upper_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "600")

    assert LlmSettings().llm_timeout_seconds == 600.0


@pytest.mark.parametrize(
    "base_url",
    ["localhost:11434/v1", "ftp://ollama:11434/v1", "http://", "not a url", ""],
    ids=["no_scheme", "ftp", "no_host", "words", "empty"],
)
def test_llm_settings_reject_non_http_base_url(
    base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", base_url)

    with pytest.raises(ValidationError, match="llm_base_url"):
        LlmSettings()


def test_llm_settings_accept_https_base_url_as_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://ollama.internal/v1")

    settings = LlmSettings()

    assert settings.llm_base_url == "https://ollama.internal/v1"
    assert isinstance(settings.llm_base_url, str)


def test_llm_settings_reject_empty_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "")

    with pytest.raises(ValidationError, match="llm_model"):
        LlmSettings()


@pytest.mark.parametrize(
    "base_url",
    ["http://xn--/v1", "http://host:abc/v1", "http://host:99999/v1", "http://\x00host/v1"],
    ids=["bad_idna", "port_not_number", "port_out_of_range", "nul_in_host"],
)
def test_llm_settings_reject_base_url_httpx_cannot_use(base_url: str) -> None:
    with pytest.raises(ValidationError, match="llm_base_url"):
        LlmSettings(llm_base_url=base_url)


def test_llm_settings_error_hides_refused_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "ftp://user:S3CRET-MARKER@ollama:11434/v1")

    with pytest.raises(ValidationError) as caught:
        LlmSettings()

    assert "S3CRET-MARKER" not in str(caught.value)


@pytest.fixture
def api_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    return monkeypatch


def test_api_settings_suggest_timeout_default(api_env: pytest.MonkeyPatch) -> None:
    assert ApiSettings().llm_suggest_timeout_seconds == 600.0


def test_api_settings_read_suggest_timeout_from_env(api_env: pytest.MonkeyPatch) -> None:
    api_env.setenv("LLM_SUGGEST_TIMEOUT_SECONDS", "550")

    assert ApiSettings().llm_suggest_timeout_seconds == 550.0


@pytest.mark.parametrize(
    ("llm", "suggest"),
    [
        ("120", "120"),
        ("120", "240"),
        ("120", "480"),
        ("120", "540"),
        ("200", "300"),
        ("600", "1000"),
        ("600", "2400"),
        ("600", "2460"),
    ],
)
def test_api_settings_reject_suggest_timeout_not_above_all_llm_attempts(
    llm: str, suggest: str, api_env: pytest.MonkeyPatch
) -> None:
    api_env.setenv("LLM_TIMEOUT_SECONDS", llm)
    api_env.setenv("LLM_SUGGEST_TIMEOUT_SECONDS", suggest)

    with pytest.raises(ValidationError, match="llm_suggest_timeout_seconds"):
        ApiSettings()


def test_api_settings_accept_suggest_timeout_above_all_llm_attempts(
    api_env: pytest.MonkeyPatch,
) -> None:
    api_env.setenv("LLM_TIMEOUT_SECONDS", "120")
    api_env.setenv("LLM_SUGGEST_TIMEOUT_SECONDS", "541")

    assert ApiSettings().llm_suggest_timeout_seconds == 541.0


def test_api_settings_accept_suggest_timeout_above_all_attempts_at_max_llm_timeout(
    api_env: pytest.MonkeyPatch,
) -> None:
    api_env.setenv("LLM_TIMEOUT_SECONDS", "600")
    api_env.setenv("LLM_SUGGEST_TIMEOUT_SECONDS", "2461")

    assert ApiSettings().llm_suggest_timeout_seconds == 2461.0


def test_api_settings_suggest_timeout_error_names_the_formula(
    api_env: pytest.MonkeyPatch,
) -> None:
    api_env.setenv("LLM_SUGGEST_TIMEOUT_SECONDS", "540")

    with pytest.raises(ValidationError, match=r"2 x 2 x llm_timeout_seconds \+ 60"):
        ApiSettings()


@pytest.mark.parametrize("timeout", ["0", "inf", "nan", "2501"])
def test_api_settings_reject_unbounded_suggest_timeout(
    timeout: str, api_env: pytest.MonkeyPatch
) -> None:
    api_env.setenv("LLM_SUGGEST_TIMEOUT_SECONDS", timeout)

    with pytest.raises(ValidationError, match="llm_suggest_timeout_seconds"):
        ApiSettings()


def test_judge_and_tool_settings_ignore_suggest_timeout(api_env: pytest.MonkeyPatch) -> None:
    api_env.setenv("LLM_TIMEOUT_SECONDS", "180")
    api_env.setenv("LLM_SUGGEST_TIMEOUT_SECONDS", "1")

    assert (LlmSettings().llm_timeout_seconds, Settings().llm_timeout_seconds) == (180.0, 180.0)
    assert not hasattr(Settings(), "llm_suggest_timeout_seconds")
