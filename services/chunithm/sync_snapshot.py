# services/chunithm/sync_snapshot.py
"""CHUNITHM-NET 동기화 결과(`SyncFetch`)를 게임 중립 `SyncSnapshot`으로 바꾼다."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from services.game.sync_types import ChartPlay, ProfileSnapshot, SyncSnapshot

if TYPE_CHECKING:
    from chuni_penguin.types import Difficulty, PersonalBest, Profile, RecentScore

_SHORT_CODE = {
    "basic": "BAS", "advanced": "ADV", "expert": "EXP",
    "master": "MAS", "ultima": "ULT", "worlds_end": "WE",
}


@dataclass(slots=True, kw_only=True)
class SyncFetch:
    profile: Profile
    best30: list[PersonalBest]
    new20: list[PersonalBest]
    recent_scores: list[RecentScore]
    # 키는 "BAS"/"ADV"/"EXP"/"MAS"/"ULT"/"WE" (chunithm_net._PERSONAL_BEST_DIFFICULTY_CODES).
    all_personal_bests: dict[str, list[PersonalBest]] = field(default_factory=dict)


def difficulty_code(difficulty: Difficulty) -> str:
    return _SHORT_CODE.get(difficulty.name, difficulty.name)


def _play_of(pb: PersonalBest) -> ChartPlay:
    return ChartPlay(
        song_title=pb.song.title,
        difficulty=difficulty_code(pb.chart.difficulty),
        score=pb.score,
        rank=str(pb.rank),
        clear_lamp=str(pb.clear_lamp),
        combo_lamp=str(pb.combo_lamp),
        chain_lamp=str(pb.chain_lamp),
    )


def _recent_of(recent: RecentScore) -> ChartPlay:
    return ChartPlay(
        song_title=recent.song.title,
        difficulty=difficulty_code(recent.chart.difficulty),
        score=recent.score,
        rank=str(recent.rank),
        achieved_at=recent.achieved_at,
    )


def snapshot_from_fetch(fetch: SyncFetch) -> SyncSnapshot:
    profile = fetch.profile
    over_power = profile.over_power
    return SyncSnapshot(
        profile=ProfileSnapshot(
            username=profile.username,
            displayed_rating=Decimal(str(profile.rating_systems[0].value)) if profile.rating_systems else None,
            level=profile.level,
            reincarnation_stars=profile.reincarnation_stars,
            character_image_url=profile.profile_picture,
            character_frame_url=profile.profile_picture_frame,
            overpower_value=over_power.value if over_power is not None else None,
            overpower_percentage=over_power.percentage if over_power is not None else None,
        ),
        best=[_play_of(pb) for pb in fetch.best30],
        new=[_play_of(pb) for pb in fetch.new20],
        recent=[_recent_of(r) for r in fetch.recent_scores],
        all_plays=[_play_of(pb) for pbs in fetch.all_personal_bests.values() for pb in pbs],
    )
