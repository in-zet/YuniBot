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
    monkeypatch.setattr("core.config.load_dotenv", lambda *args, **kwargs: None)
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


def test_load_config_llm_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    monkeypatch.delenv("SUMMARY_MODEL", raising=False)
    monkeypatch.delenv("FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("THINKING_LEVEL", raising=False)
    monkeypatch.delenv("SKILL_LOADING_MODE", raising=False)
    monkeypatch.delenv("MAX_TOOL_CALLS_PER_TURN", raising=False)
    monkeypatch.delenv("MAX_TOKENS_PER_TURN", raising=False)

    config = load_config()

    assert config.fallback_model == ""  # 비우면 보조 모델 없음
    assert config.gemini_api_key == ""
    assert config.agent_model == "gemini-3.8-flash"
    assert config.summary_model == "gemini-3.5-flash-lite"
    assert config.thinking_level == "low"
    assert config.skill_loading_mode == "instructions"
    assert config.max_tool_calls_per_turn == 5
    assert config.max_tokens_per_turn == 50000


def test_load_config_llm_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("AGENT_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("SUMMARY_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("FALLBACK_MODEL", " gemini-3.5-flash-lite ")
    monkeypatch.setenv("THINKING_LEVEL", "high")
    monkeypatch.setenv("SKILL_LOADING_MODE", "full")
    monkeypatch.setenv("MAX_TOOL_CALLS_PER_TURN", "3")
    monkeypatch.setenv("MAX_TOKENS_PER_TURN", "20000")

    config = load_config()

    assert config.gemini_api_key == "fake-key"
    assert config.agent_model == "gemini-3.5-flash-lite"
    assert config.summary_model == "gemini-3.1-flash-lite"
    assert config.fallback_model == "gemini-3.5-flash-lite"
    assert config.thinking_level == "high"
    assert config.skill_loading_mode == "full"
    assert config.max_tool_calls_per_turn == 3
    assert config.max_tokens_per_turn == 20000


def test_load_config_llm_provider_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    config = load_config()

    assert config.llm_provider == "gemini"
    assert config.anthropic_api_key == ""


def test_load_config_llm_provider_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic-key")

    config = load_config()

    assert config.llm_provider == "claude"
    assert config.anthropic_api_key == "fake-anthropic-key"


def test_load_config_websearch_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.delenv("WEBSEARCH_PROVIDER", raising=False)
    monkeypatch.delenv("SEARCH_MODEL", raising=False)
    monkeypatch.delenv("WEBSEARCH_COOLDOWN_SECONDS", raising=False)

    config = load_config()

    assert config.websearch_provider == "gemini"
    assert config.search_model == "gemini-3.8-flash"
    assert config.websearch_cooldown_seconds == 15


def test_load_config_websearch_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    # websearch_provider는 llm_provider를 안 건드린다 — 둘이 독립이라는 게 핵심이다.
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("WEBSEARCH_PROVIDER", "claude")
    monkeypatch.setenv("SEARCH_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("WEBSEARCH_COOLDOWN_SECONDS", "5")

    config = load_config()

    assert config.llm_provider == "gemini"
    assert config.websearch_provider == "claude"
    assert config.search_model == "claude-haiku-5-5"
    assert config.websearch_cooldown_seconds == 5
