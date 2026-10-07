from typing import Final
from urllib.parse import urlsplit

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HTTP_SCHEMES: Final = frozenset({"http", "https"})


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = Field("qwen3:4b", min_length=1)
    llm_timeout_seconds: float = Field(120.0, gt=0, le=600, allow_inf_nan=False)
    llm_explain_limit: int = Field(10, ge=0, le=100)

    @field_validator("llm_base_url")
    @classmethod
    def _require_http_url(cls, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in _HTTP_SCHEMES or not parts.hostname:
            raise ValueError("must be an http(s) URL with a host")
        try:
            _ = parts.port, httpx.URL(url).host
        except (httpx.InvalidURL, ValueError) as error:
            raise ValueError("must be a URL httpx can use") from error
        return url


class Settings(LlmSettings):
    database_url: str
