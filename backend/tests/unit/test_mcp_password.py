import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.db.mcp_password import McpPasswordSettings, scram_sha256_verifier

SALT = bytes(range(16))
VALID_PASSWORD = "Gq3_xV9-k2Lp.T~7wZr4Nf8sYb1HcJ6d"
MARKER = "Zq9-marker-secret-value"
BAD_PASSWORD_MESSAGE = "password must be 16-128 characters from A-Z a-z 0-9 . _ ~ -"
ENV_VARS = ("DATABASE_URL", "MCP_DATABASE_URL", "MCP_COMPANY_ID")


def _mcp_url(password: str, user: str = "mcp_server") -> str:
    return f"postgresql+asyncpg://{user}:{password}@localhost:5432/workforceguard"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _load(monkeypatch: pytest.MonkeyPatch, mcp_database_url: str) -> McpPasswordSettings:
    monkeypatch.setenv("MCP_DATABASE_URL", mcp_database_url)
    return McpPasswordSettings()


def test_scram_verifier_has_postgres_format() -> None:
    verifier = scram_sha256_verifier(VALID_PASSWORD, SALT)

    assert re.fullmatch(
        r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+", verifier
    )


def test_scram_verifier_matches_known_vector() -> None:
    assert scram_sha256_verifier("pencil", SALT) == (
        "SCRAM-SHA-256$4096:AAECAwQFBgcICQoLDA0ODw==$"
        "zHCdol2044/ZyWzPLi7oxApCkamKw9Z+E4U/QApd/5Y=:"
        "dd5peBOitVnLNFu7VmwP+HiDaaw4OUCv396eVCWhYiE="
    )


def test_scram_verifier_same_salt_is_deterministic() -> None:
    assert scram_sha256_verifier(VALID_PASSWORD, SALT) == scram_sha256_verifier(
        VALID_PASSWORD, SALT
    )


def test_scram_verifier_different_salt_differs() -> None:
    assert scram_sha256_verifier(VALID_PASSWORD, SALT) != scram_sha256_verifier(
        VALID_PASSWORD, bytes(16)
    )


def test_scram_verifier_never_contains_password() -> None:
    assert VALID_PASSWORD not in scram_sha256_verifier(VALID_PASSWORD, SALT)


def test_mcp_password_settings_reads_mcp_password_from_env_mcp(isolated_env: Path) -> None:
    (isolated_env / ".env.mcp").write_text(f"MCP_DATABASE_URL={_mcp_url(VALID_PASSWORD)}\n")

    settings = McpPasswordSettings()

    assert settings.mcp_password().get_secret_value() == VALID_PASSWORD


def test_mcp_password_settings_ignores_mcp_url_in_env_file(isolated_env: Path) -> None:
    (isolated_env / ".env").write_text(f"MCP_DATABASE_URL={_mcp_url(VALID_PASSWORD)}\n")

    with pytest.raises(ValidationError, match="mcp_database_url"):
        McpPasswordSettings()


def test_mcp_password_settings_missing_mcp_url_is_rejected() -> None:
    with pytest.raises(ValidationError, match="mcp_database_url"):
        McpPasswordSettings()


def test_mcp_password_settings_wrong_user_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="must log in as mcp_server"):
        _load(monkeypatch, _mcp_url(VALID_PASSWORD, user="workforceguard"))


@pytest.mark.parametrize(
    "mcp_database_url",
    [
        _mcp_url("a" * 15),
        _mcp_url("a" * 129),
        _mcp_url("a" * 16 + "'"),
        _mcp_url("a" * 16 + "%40"),
        _mcp_url("a" * 16 + "%25"),
        _mcp_url("a" * 16 + "%C3%A9"),
        _mcp_url("a" * 16 + "%21"),
        "postgresql+asyncpg://mcp_server:@localhost:5432/workforceguard",
        "postgresql+asyncpg://mcp_server@localhost:5432/workforceguard",
    ],
    ids=["15-chars", "129-chars", "quote", "at", "percent", "non-ascii", "bang", "empty", "none"],
)
def test_mcp_password_settings_bad_password_is_rejected(
    monkeypatch: pytest.MonkeyPatch, mcp_database_url: str
) -> None:
    with pytest.raises(ValidationError, match=re.escape(BAD_PASSWORD_MESSAGE)):
        _load(monkeypatch, mcp_database_url)


@pytest.mark.parametrize(
    "mcp_database_url",
    [
        _mcp_url(MARKER, user="workforceguard"),
        _mcp_url(MARKER + "%21"),
        f"not a url {MARKER}",
    ],
    ids=["wrong-user", "bad-charset", "unparseable"],
)
def test_mcp_password_settings_error_never_contains_password(
    monkeypatch: pytest.MonkeyPatch, mcp_database_url: str
) -> None:
    with pytest.raises(ValidationError, match="mcp_database_url") as caught:
        _load(monkeypatch, mcp_database_url)

    assert MARKER not in str(caught.value)
    assert MARKER not in repr(caught.value)


def test_mcp_password_settings_accepts_generated_style_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _load(monkeypatch, _mcp_url(VALID_PASSWORD))

    assert settings.mcp_password().get_secret_value() == VALID_PASSWORD
