# tests/core/test_config.py
import datetime
from pathlib import Path

import pytest

from core.config import load_config


def test_load_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("SKILLS_DIRS", "skills,tests/fixtures/skills_ok")
    monkeypatch.setenv("DB_PATH", "data/test.db")
    monkeypatch.setenv("TEST_GUILD_ID", "123456789")

    config = load_config()

    assert config.discord_token == "fake-token"
    assert config.skills_dirs == [Path("skills"), Path("tests/fixtures/skills_ok")]
    assert config.db_path == Path("data/test.db")
    assert config.test_guild_id == 123456789


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.delenv("SKILLS_DIRS", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("TEST_GUILD_ID", raising=False)

    config = load_config()

    assert config.skills_dirs == [Path("skills")]
    assert config.db_path == Path("data/yuni.db")
    assert config.test_guild_id is None


def test_load_config_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="DISCORD_TOKEN"):
        load_config()


def test_load_config_parses_admin_ids_and_quiet_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("ADMIN_DISCORD_IDS", "111, 222 ,111")
    monkeypatch.setenv("QUIET_HOURS_START", "02:00")
    monkeypatch.setenv("QUIET_HOURS_END", "06:00")
    monkeypatch.setenv("COOKIE_ENCRYPTION_KEY", "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9n")

    config = load_config()

    assert config.admin_discord_ids == frozenset({"111", "222"})
    assert config.quiet_hours_start == datetime.time(2, 0)
    assert config.quiet_hours_end == datetime.time(6, 0)
    assert config.cookie_encryption_key == b"Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9n"


def test_load_config_defaults_admin_ids_and_quiet_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.delenv("ADMIN_DISCORD_IDS", raising=False)
    monkeypatch.delenv("QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("QUIET_HOURS_END", raising=False)
    monkeypatch.delenv("COOKIE_ENCRYPTION_KEY", raising=False)

    config = load_config()

    assert config.admin_discord_ids == frozenset()
    assert config.quiet_hours_start == datetime.time(2, 0)
    assert config.quiet_hours_end == datetime.time(6, 0)
    assert config.cookie_encryption_key == b""
