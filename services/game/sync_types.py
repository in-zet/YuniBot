# services/game/sync_types.py
"""동기화 한 번이 `RecordsService`에 넘기는 게임 중립 자료형.

게임마다 NET 어댑터가 자기 응답(CHUNITHM은 vendored `PersonalBest` 등, maimai는 파서 결과)을 이 모양으로
바꿔 넘긴다. `RecordsService`는 이 모양만 안다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True, kw_only=True, frozen=True)
class ChartPlay:
    """채보 하나의 기록 또는 플레이 한 번. 램프는 저장 문자열 그대로(우열은 프로필의 램프 순서표가 안다)."""

    song_title: str
    difficulty: str  # 프로필의 chart_codes 중 하나("MAS")
    chart_type: str = ""  # 채보 종류(maimai "STD"/"DX"), 없는 게임은 ""
    score: int
    rank: str
    clear_lamp: str = ""
    combo_lamp: str = ""
    chain_lamp: str = ""
    dx_score: int | None = None
    achieved_at: datetime | None = None  # 최근 플레이에서만 의미가 있다


@dataclass(slots=True, kw_only=True, frozen=True)
class ProfileSnapshot:
    username: str
    displayed_rating: Decimal | None = None  # NET이 보여 주는 레이팅(없으면 대조하지 않는다)
    level: int | None = None
    reincarnation_stars: int | None = None
    character_image_url: str | None = None
    character_frame_url: str | None = None
    overpower_value: float | None = None
    overpower_percentage: float | None = None


@dataclass(slots=True, kw_only=True)
class SyncSnapshot:
    profile: ProfileSnapshot
    # NET이 베스트/신곡 목록을 따로 주는 게임이면 그 목록(레이팅 계산의 기준). 안 주면 None이고, 서비스가
    # 전체 기록과 곡 DB의 신곡 판정으로 프레임을 직접 짠다.
    best: list[ChartPlay] | None = None
    new: list[ChartPlay] | None = None
    recent: list[ChartPlay] = field(default_factory=list)
    all_plays: list[ChartPlay] = field(default_factory=list)
