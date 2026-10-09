# tests/core/test_config.py
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
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.delenv("SKILLS_DIRS", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("TEST_GUILD_ID", raising=False)

    config = load_config()

    assert config.skills_dirs == [Path("skills")]
    assert config.db_path == Path("data/yuni.db")
    assert config.test_guild_id is None


def test_load_config_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="DISCORD_TOKEN"):
        load_config()
