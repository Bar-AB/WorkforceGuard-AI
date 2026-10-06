import asyncio
import base64
import hashlib
import hmac
import re
import secrets
from typing import Final

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.urls import parse_secret_database_url

MCP_LOGIN_ROLE: Final = "mcp_server"
SCRAM_ITERATIONS: Final = 4096
SALT_BYTES: Final = 16
PASSWORD_PATTERN: Final = re.compile(r"[A-Za-z0-9._~-]{16,128}")
SET_VERIFIER = text("SELECT set_config('wg.mcp_password_verifier', :verifier, true)")
ALTER_PASSWORD = text(
    f"DO $$ BEGIN EXECUTE format('ALTER ROLE %I PASSWORD %L', '{MCP_LOGIN_ROLE}', "
    "current_setting('wg.mcp_password_verifier')); END $$"
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def scram_sha256_verifier(password: str, salt: bytes, iterations: int = SCRAM_ITERATIONS) -> str:
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    server_key = hmac.digest(salted, b"Server Key", "sha256")
    stored_key = hashlib.sha256(client_key).digest()
    return f"SCRAM-SHA-256${iterations}:{_b64(salt)}${_b64(stored_key)}:{_b64(server_key)}"


class McpPasswordSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.mcp", extra="ignore", hide_input_in_errors=True
    )

    mcp_database_url: SecretStr

    @field_validator("mcp_database_url", mode="after")
    @classmethod
    def _require_mcp_login(cls, value: SecretStr) -> SecretStr:
        url = parse_secret_database_url(value)
        if url.username != MCP_LOGIN_ROLE:
            raise ValueError(f"must log in as {MCP_LOGIN_ROLE}")
        if not PASSWORD_PATTERN.fullmatch(url.password or ""):
            raise ValueError("password must be 16-128 characters from A-Z a-z 0-9 . _ ~ -")
        return value

    def mcp_password(self) -> SecretStr:
        return SecretStr(parse_secret_database_url(self.mcp_database_url).password or "")


async def set_mcp_server_password(
    admin_database_url: str, password: SecretStr, salt: bytes
) -> None:
    verifier = scram_sha256_verifier(password.get_secret_value(), salt)
    engine = create_async_engine(admin_database_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(SET_VERIFIER, {"verifier": verifier})
            await conn.execute(ALTER_PASSWORD)
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(
        set_mcp_server_password(
            Settings().database_url,
            McpPasswordSettings().mcp_password(),
            secrets.token_bytes(SALT_BYTES),
        )
    )
    print(f"{MCP_LOGIN_ROLE} password set.")


if __name__ == "__main__":
    main()
