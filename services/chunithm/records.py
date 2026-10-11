# services/chunithm/records.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import aiosqlite

from core.tool_types import ToolContext
from services.chunithm.records_calc import (
    allowed_attacks,
    assign_session_date,
    average_rating,
    chart_rating,
    remaining_to_target,
)
from services.chunithm.songdb import SongDbService
from services.common.players import PlayersService

if TYPE_CHECKING:
    from chuni_penguin.types import PersonalBest

    from services.chunithm.chunithm_net import SyncFetch

_GAME = "chunithm"
# records_pb.rating / records_sync_log.computed_rating은 TEXT NOT NULL이라 None을
# 그대로 넣을 수 없다 — 상수를 몰라 계산하지 못한 값은 이 표시로 남긴다.
_UNKNOWN_RATING = "계산 불가"


def _rating_str(value: Decimal | None) -> str:
    return str(value) if value is not None else _UNKNOWN_RATING


@dataclass(slots=True, kw_only=True)
class SyncResult:
    is_first_sync: bool
    # 상수를 모르는 채보가 하나라도 있으면 None("계산 불가")이다.
    computed_rating: Decimal | None
    best30_average: Decimal | None
    new20_average: Decimal | None
    displayed_rating: Decimal | None
    rating_mismatch: bool
    session_ids: list[str]


class RecordsService:
    """기록 스냅샷, 베스트, 세션 id, 레이팅 계산과 CHUNITHM-NET 대조."""

    def __init__(self, *, conn: aiosqlite.Connection, songdb: SongDbService, players: PlayersService) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row
        self._songdb = songdb
        self._players = players

    async def save_sync(self, player_id: str, fetch: SyncFetch, ctx: ToolContext) -> SyncResult:
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM records_sync_log WHERE player_id = ? AND game = ?",
            (player_id, _GAME),
        )
        row = await cursor.fetchone()
        assert row is not None
        is_first_sync = row[0] == 0

        synced_at = datetime.now(UTC)
        # 세션 날짜 경계(새벽 4시)는 로컬 벽시계 기준이다(assign_session_date 참고,
        # core/bot.py의 cron/조용한 시간도 로컬 datetime.now()를 쓴다). 저장 타임스탬프
        # (synced_at)는 다른 테이블들처럼 UTC로 유지한다.
        session_date_basis = datetime.now()  # noqa: DTZ005
        session_ids: list[str] = []

        best30_ratings = await self._save_personal_bests(
            player_id, fetch.best30, synced_at, session_date_basis, session_ids
        )
        new20_ratings = await self._save_personal_bests(
            player_id, fetch.new20, synced_at, session_date_basis, session_ids
        )

        # "베스트 평균"(통과 기준)은 베스트30과 신곡20을 각각 따로 평균한 값이다 — 둘을
        # 합쳐 50으로 나눈 전체 레이팅과는 다른 숫자다. CHUNITHM-NET이 이 둘을 텍스트로
        # 따로 보여주는 페이지는 없어서 저장본과 직접 대조할 수는 없지만, 평균을 공식대로
        # (30곡/20곡 가중)다시 합치면 이미 저장본으로 검산된 computed_rating과 정확히
        # 같아야 한다 — Task 9 Step 5의 일관성 테스트가 그걸 확인한다.
        best30_average = average_rating(best30_ratings)
        new20_average = average_rating(new20_ratings)
        computed_rating = average_rating(best30_ratings + new20_ratings)

        displayed_rating = None
        if fetch.profile.rating_systems:
            displayed_rating = Decimal(str(fetch.profile.rating_systems[0].value))
        # 계산 불가(None)인데 표시값이 있으면 대조할 수 없으므로 불일치로 남긴다.
        rating_mismatch = displayed_rating is not None and computed_rating != displayed_rating

        await self._conn.execute(
            "INSERT INTO records_sync_log (id, player_id, game, synced_at, nickname, "
            "displayed_rating, computed_rating, rating_mismatch, is_first_sync) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), player_id, _GAME, synced_at.isoformat(), fetch.profile.username,
                str(displayed_rating) if displayed_rating is not None else None,
                _rating_str(computed_rating), int(rating_mismatch), int(is_first_sync),
            ),
        )
        await self._conn.commit()

        if is_first_sync:
            await self._players.set_status_tag(player_id, "chunithm-synced", source="records:first_sync")

        return SyncResult(
            is_first_sync=is_first_sync,
            computed_rating=computed_rating,
            best30_average=best30_average,
            new20_average=new20_average,
            displayed_rating=displayed_rating,
            rating_mismatch=rating_mismatch,
            session_ids=session_ids,
        )

    async def best_score(self, player_id: str, *, song_title: str, difficulty: str) -> int | None:
        cursor = await self._conn.execute(
            "SELECT score FROM records_pb WHERE player_id = ? AND game = ? AND song_title = ? AND difficulty = ?",
            (player_id, _GAME, song_title, difficulty),
        )
        row = await cursor.fetchone()
        return row["score"] if row is not None else None

    async def last_sync_at(self, player_id: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT synced_at FROM records_sync_log WHERE player_id = ? AND game = ? "
            "ORDER BY synced_at DESC LIMIT 1",
            (player_id, _GAME),
        )
        row = await cursor.fetchone()
        return row["synced_at"] if row is not None else None

    @staticmethod
    def allowed_attacks(note_count: int | None, target_score: int) -> Decimal | None:
        """chunithm-border 스킬이 쓰는 순수 계산 패스스루(services/chunithm/records_calc.py)."""
        return allowed_attacks(note_count, target_score)

    @staticmethod
    def remaining_to_target(
        note_count: int | None, current_score: int, target_score: int
    ) -> tuple[int, Decimal] | None:
        return remaining_to_target(note_count, current_score, target_score)

    async def _save_personal_bests(
        self,
        player_id: str,
        records: list[PersonalBest],
        synced_at: datetime,
        session_date_basis: datetime,
        session_ids: list[str],
    ) -> list[Decimal | None]:
        """베스트30 또는 신곡20 한 쪽을 저장하고, 그 쪽만의 레이팅 리스트를 돌려준다
        (호출하는 쪽이 베스트/신곡 평균과 전체 평균을 각각 계산할 수 있게).
        synced_at(UTC)은 저장 타임스탬프에, session_date_basis(로컬 벽시계)는 세션
        날짜 배정에만 쓴다."""
        ratings: list[Decimal | None] = []
        for pb in records:
            title = pb.song.title
            difficulty = pb.chart.difficulty.name
            difficulty_code = _SHORT_CODE[difficulty] if difficulty in _SHORT_CODE else difficulty  # noqa: SIM401
            const = self._songdb.const_for(title, difficulty_code)
            rating = chart_rating(pb.score, const)
            ratings.append(rating)

            session_id = await self._assign_session(player_id, session_date_basis)
            if session_id not in session_ids:
                session_ids.append(session_id)

            # 점수가 실제로 올랐을 때만 행 전체(점수·랭크·램프·상수·레이팅·세션·시각)를
            # 한 단위로 바꾼다. 개선이 없으면 아무것도 바꾸지 않는다 — "이 기록을 언제
            # 세웠나"(session_id/updated_at)는 5단계 세션·리포트 스킬이 쓰는 정보다.
            await self._conn.execute(
                "INSERT INTO records_pb (player_id, game, song_title, difficulty, score, rank, "
                "clear_lamp, combo_lamp, chain_lamp, const, rating, session_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (player_id, game, song_title, difficulty) DO UPDATE SET "
                "score = excluded.score, rank = excluded.rank, clear_lamp = excluded.clear_lamp, "
                "combo_lamp = excluded.combo_lamp, chain_lamp = excluded.chain_lamp, "
                "const = excluded.const, rating = excluded.rating, session_id = excluded.session_id, "
                "updated_at = excluded.updated_at "
                "WHERE excluded.score > records_pb.score",
                (
                    player_id, _GAME, title, difficulty_code, pb.score, str(pb.rank),
                    str(pb.clear_lamp), str(pb.combo_lamp), str(pb.chain_lamp),
                    const, _rating_str(rating), session_id, synced_at.isoformat(),
                ),
            )
        return ratings

    async def _assign_session(self, player_id: str, played_at: datetime) -> str:
        play_date = assign_session_date(played_at)
        cursor = await self._conn.execute(
            "SELECT id FROM records_sessions WHERE player_id = ? AND game = ? AND play_date = ?",
            (player_id, _GAME, play_date.isoformat()),
        )
        row = await cursor.fetchone()
        if row is not None:
            return row["id"]

        session_id = str(uuid.uuid4())
        await self._conn.execute(
            "INSERT INTO records_sessions (id, player_id, game, play_date, estimated, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (session_id, player_id, _GAME, play_date.isoformat(), datetime.now(UTC).isoformat()),
        )
        return session_id


_SHORT_CODE = {
    "basic": "BAS", "advanced": "ADV", "expert": "EXP",
    "master": "MAS", "ultima": "ULT", "worlds_end": "WE",
}
