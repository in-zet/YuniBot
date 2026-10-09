# core/config.py
from __future__ import annotations

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

    return CoreConfig(
        discord_token=token,
        skills_dirs=skills_dirs,
        db_path=db_path,
        test_guild_id=test_guild_id,
    )
