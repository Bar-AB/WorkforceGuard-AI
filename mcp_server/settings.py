import uuid

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db.urls import parse_secret_database_url


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.mcp", extra="ignore", hide_input_in_errors=True
    )

    mcp_database_url: SecretStr
    mcp_company_id: uuid.UUID

    @field_validator("mcp_database_url", mode="after")
    @classmethod
    def _require_parseable(cls, value: SecretStr) -> SecretStr:
        parse_secret_database_url(value)
        return value
