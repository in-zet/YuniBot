# core/config.py
from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from dotenv import load_dotenv


@dataclass(slots=True, kw_only=True)
class CoreConfig:
    discord_token: str
    skills_dirs: list[Path]
    db_path: Path
    test_guild_id: int | None
    admin_discord_ids: frozenset[str]
    quiet_hours_start: datetime.time
    quiet_hours_end: datetime.time
    cookie_encryption_key: bytes
    # 기동 시 Chunimaru songdb를 받아 덧씌울지. 테스트가 실제 사이트에 접속하지 않도록
    # 끌 수 있게 둔 스위치다(운영 기본값은 켬).
    fetch_chunimaru_on_startup: bool = True
    # 제공자는 부팅 시점 설정값이다(런타임 전환 없음). agent_model/summary_model의 값은
    # 고른 제공자에 맞는 모델 이름이어야 한다(예: claude면 "claude-haiku-5-5").
    llm_provider: Literal["gemini", "claude"] = "gemini"
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    agent_model: str = "gemini-3.8-flash"
    summary_model: str = "gemini-3.5-flash-lite"
    # 메인 모델이 일시 오류(504 등)로 끝내 실패하면 같은 요청을 이 모델로 한 번 더 보낸다. 비우면 끈다.
    # Gemini 제공자(LLM_PROVIDER=gemini)일 때만 쓴다.
    fallback_model: str = ""
    # websearch는 LLM_PROVIDER와 독립이다 — 메인 에이전트와 검색이 다른 제공자를 쓸 수
    # 있다(00-core.md "websearch" 서비스).
    websearch_provider: Literal["gemini", "claude"] = "gemini"
    search_model: str = "gemini-3.8-flash"
    websearch_cooldown_seconds: int = 15
    thinking_level: Literal["low", "medium", "high"] = "low"
    skill_loading_mode: Literal["instructions", "full"] = "instructions"
    max_tool_calls_per_turn: int = 5
    max_tokens_per_turn: int = 50000


def _parse_time(raw: str, default: datetime.time) -> datetime.time:
    if not raw:
        return default
    hour, _, minute = raw.partition(":")
    return datetime.time(int(hour), int(minute))


def load_config() -> CoreConfig:
    """환경변수에서 코어 설정을 읽는다. 이미 설정된 환경변수는 .env로 덮어쓰지 않는다."""
    load_dotenv(override=False)

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN 환경변수가 없다")

    skills_dirs_raw = os.environ.get("SKILLS_DIRS", "skills")
    skills_dirs = [Path(part.strip()) for part in skills_dirs_raw.split(",") if part.strip()]

    db_path = Path(os.environ.get("DB_PATH", "data/yuni.db"))

    guild_raw = os.environ.get("TEST_GUILD_ID")
    test_guild_id = int(guild_raw) if guild_raw else None

    admin_ids_raw = os.environ.get("ADMIN_DISCORD_IDS", "")
    admin_discord_ids = frozenset(
        part.strip() for part in admin_ids_raw.split(",") if part.strip()
    )

    quiet_start = _parse_time(os.environ.get("QUIET_HOURS_START", ""), datetime.time(2, 0))
    quiet_end = _parse_time(os.environ.get("QUIET_HOURS_END", ""), datetime.time(6, 0))

    cookie_key = os.environ.get("COOKIE_ENCRYPTION_KEY", "").encode()

    llm_provider_raw = os.environ.get("LLM_PROVIDER", "gemini")
    gemini_api_key = os.environ.get("GEMINI_API_KEY", "")
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    agent_model = os.environ.get("AGENT_MODEL", "gemini-3.8-flash")
    summary_model = os.environ.get("SUMMARY_MODEL", "gemini-3.5-flash-lite")
    fallback_model = os.environ.get("FALLBACK_MODEL", "").strip()
    websearch_provider_raw = os.environ.get("WEBSEARCH_PROVIDER", "gemini")
    search_model = os.environ.get("SEARCH_MODEL", "gemini-3.8-flash")
    websearch_cooldown_seconds = int(os.environ.get("WEBSEARCH_COOLDOWN_SECONDS", "15"))
    thinking_level_raw = os.environ.get("THINKING_LEVEL", "low")
    skill_loading_mode_raw = os.environ.get("SKILL_LOADING_MODE", "instructions")
    max_tool_calls_per_turn = int(os.environ.get("MAX_TOOL_CALLS_PER_TURN", "5"))
    max_tokens_per_turn = int(os.environ.get("MAX_TOKENS_PER_TURN", "50000"))

    return CoreConfig(
        discord_token=token,
        skills_dirs=skills_dirs,
        db_path=db_path,
        test_guild_id=test_guild_id,
        admin_discord_ids=admin_discord_ids,
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
        cookie_encryption_key=cookie_key,
        llm_provider=cast("Literal['gemini', 'claude']", llm_provider_raw),
        gemini_api_key=gemini_api_key,
        anthropic_api_key=anthropic_api_key,
        agent_model=agent_model,
        summary_model=summary_model,
        fallback_model=fallback_model,
        websearch_provider=cast("Literal['gemini', 'claude']", websearch_provider_raw),
        search_model=search_model,
        websearch_cooldown_seconds=websearch_cooldown_seconds,
        thinking_level=cast("Literal['low', 'medium', 'high']", thinking_level_raw),
        skill_loading_mode=cast("Literal['instructions', 'full']", skill_loading_mode_raw),
        max_tool_calls_per_turn=max_tool_calls_per_turn,
        max_tokens_per_turn=max_tokens_per_turn,
    )
