# core/config.py
from __future__ import annotations

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

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

    return CoreConfig(
        discord_token=token,
        skills_dirs=skills_dirs,
        db_path=db_path,
        test_guild_id=test_guild_id,
        admin_discord_ids=admin_discord_ids,
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
        cookie_encryption_key=cookie_key,
    )
