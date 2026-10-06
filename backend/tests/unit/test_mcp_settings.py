import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from mcp_server.settings import McpSettings

MCP_DATABASE_URL = "postgresql+asyncpg://mcp_server:mcp-login-password-1@db:5432/x"
ADMIN_URL = "postgresql+asyncpg://workforceguard:admin-marker-pw@localhost/db"
COMPANY_A = uuid.UUID("00000000-0000-0000-0000-00000000000a")
COMPANY_B = uuid.UUID("00000000-0000-0000-0000-00000000000b")
MARKER = "Zq9-marker-secret-value"
ENV_VARS = ("DATABASE_URL", "MCP_DATABASE_URL", "MCP_COMPANY_ID", "POSTGRES_PASSWORD")


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_mcp_settings_missing_company_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_DATABASE_URL", MCP_DATABASE_URL)

    with pytest.raises(ValidationError, match="mcp_company_id"):
        McpSettings()


def test_mcp_settings_invalid_company_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_DATABASE_URL", MCP_DATABASE_URL)
    monkeypatch.setenv("MCP_COMPANY_ID", "not-a-uuid")

    with pytest.raises(ValidationError, match="mcp_company_id"):
        McpSettings()


def test_mcp_settings_missing_database_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_COMPANY_ID", str(COMPANY_A))

    with pytest.raises(ValidationError, match="mcp_database_url"):
        McpSettings()


def test_mcp_settings_reads_only_env_mcp_file(isolated_env: Path) -> None:
    (isolated_env / ".env").write_text(
        f"DATABASE_URL={ADMIN_URL}\n"
        "POSTGRES_PASSWORD=admin-marker-pw\n"
        f"MCP_DATABASE_URL={ADMIN_URL}\n"
        f"MCP_COMPANY_ID={COMPANY_A}\n"
    )
    (isolated_env / ".env.mcp").write_text(
        f"MCP_DATABASE_URL={MCP_DATABASE_URL}\nMCP_COMPANY_ID={COMPANY_B}\n"
    )

    settings = McpSettings()

    assert settings.mcp_company_id == COMPANY_B
    assert settings.mcp_database_url.get_secret_value() == MCP_DATABASE_URL
    assert "admin-marker-pw" not in repr(settings)
    assert "admin-marker-pw" not in str(settings.model_dump())
    assert set(McpSettings.model_fields) == {"mcp_database_url", "mcp_company_id"}
    assert McpSettings.model_config["env_file"] == ".env.mcp"


def test_mcp_settings_ignores_env_file(isolated_env: Path) -> None:
    (isolated_env / ".env").write_text(
        f"MCP_DATABASE_URL={MCP_DATABASE_URL}\nMCP_COMPANY_ID={COMPANY_A}\n"
    )

    with pytest.raises(ValidationError, match="mcp_database_url"):
        McpSettings()


def test_mcp_settings_repr_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_DATABASE_URL", MCP_DATABASE_URL)
    monkeypatch.setenv("MCP_COMPANY_ID", str(COMPANY_A))

    settings = McpSettings()

    assert "mcp-login-password-1" not in repr(settings)
    assert "mcp-login-password-1" not in str(settings)


def test_mcp_settings_error_never_contains_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_DATABASE_URL", f"not a url {MARKER}")
    monkeypatch.setenv("MCP_COMPANY_ID", "not-a-uuid")

    with pytest.raises(ValidationError, match="mcp_database_url") as caught:
        McpSettings()

    assert MARKER not in str(caught.value)
    assert MARKER not in repr(caught.value)
