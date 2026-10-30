# services/maimai/sync_snapshot.py
"""maimai DX NET 페이지 파서 결과를 게임 중립 `SyncSnapshot`으로 바꾼다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from services.game.sync_types import ChartPlay, ProfileSnapshot, SyncSnapshot
from services.maimai.parser import MaimaiProfile, PlayEntry, RatingTargets, ScoreEntry


def _play_of(entry: ScoreEntry) -> ChartPlay:
    """콤보(FC·AP)는 `combo_lamp`, 싱크(FS·FDX)는 `chain_lamp`에 담는다. 클리어 램프는 없다."""
    return ChartPlay(
        song_title=entry.title,
        difficulty=entry.difficulty,
        chart_type=entry.chart_type,
        score=entry.achievement,
        rank=entry.rank,
        combo_lamp=entry.combo_lamp,
        chain_lamp=entry.sync_lamp,
        dx_score=entry.dx_score,
    )


def _recent_of(play: PlayEntry) -> ChartPlay:
    return ChartPlay(
        song_title=play.title,
        difficulty=play.difficulty,
        chart_type=play.chart_type,
        score=play.achievement,
        rank=play.rank,
        combo_lamp=play.combo_lamp,
        chain_lamp=play.sync_lamp,
        dx_score=play.dx_score,
        achieved_at=play.played_at,
    )


def snapshot_from_pages(
    profile: MaimaiProfile,
    targets: RatingTargets,
    recent: Sequence[PlayEntry],
    lists: Mapping[str, Sequence[ScoreEntry]],
) -> SyncSnapshot:
    """`lists`는 난이도 코드 → 그 난이도에서 친 곡 전부. 레이팅 대상곡 목록(구곡 35·신곡 15)은 `best`·`new`로
    그대로 넘기고, 램프·DX 점수는 전체 기록에서 온다(대상곡 페이지에는 없다)."""
    return SyncSnapshot(
        profile=ProfileSnapshot(
            username=profile.name,
            displayed_rating=Decimal(profile.rating),
            character_image_url=profile.icon_url,
        ),
        best=[_play_of(entry) for entry in targets.old],
        new=[_play_of(entry) for entry in targets.new],
        recent=[_recent_of(play) for play in recent],
        all_plays=[_play_of(entry) for entries in lists.values() for entry in entries],
    )
