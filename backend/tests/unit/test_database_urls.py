import pytest
from pydantic import SecretStr

from app.db.urls import parse_secret_database_url


def test_parse_secret_database_url_returns_url() -> None:
    url = parse_secret_database_url(
        SecretStr("postgresql+asyncpg://mcp_server:pw@db:5433/workforceguard")
    )

    assert (url.username, url.password, url.host, url.port, url.database) == (
        "mcp_server",
        "pw",
        "db",
        5433,
        "workforceguard",
    )


def test_parse_secret_database_url_unparseable_error_never_contains_input() -> None:
    with pytest.raises(ValueError, match="is not a valid database URL") as caught:
        parse_secret_database_url(SecretStr("not a url hunter2-secret-value"))

    assert "hunter2-secret-value" not in str(caught.value)
    assert caught.value.__cause__ is None
