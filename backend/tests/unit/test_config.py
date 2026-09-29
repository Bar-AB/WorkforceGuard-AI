import pytest

from app.config import Settings


def test_settings_database_url_is_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/x"
