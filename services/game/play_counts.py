# services/game/play_counts.py
"""채보 플레이 횟수(플카) 저장과 수집(2026-11-28 사용자 결정).

NET 목록에는 플레이 횟수가 없고 곡 상세에만 있어 곡마다 요청 하나가 든다. 동기화 뒤에 배경에서 받아 저장하고, 도구는
저장값을 읽는다. 매번 전체를 받지 않도록, NET 플레이어 정보의 총 플레이 횟수(크레딧)가 직전 수집 뒤로 늘어난 양과 최근
플레이 목록에 새로 잡힌 크레딧 수(곡 번호 1인 판)가 같으면 그 판들의 곡만 다시 받는다. 다르면 최근 목록 밖에서 친 판이
있다는 뜻이라(최근 목록은 길이가 정해져 있지 않다 — CHUNITHM 33판, maimai 50판, 2026-11-28 실측) 친 곡 전체를 다시 받는다.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import aiosqlite

    from core.tool_types import ToolContext
    from services.game.records import RecordsService
    from services.game.songdb import SongDb

logger = logging.getLogger(__name__)

# 저장 키: (song_key, chart_type). CHUNITHM은 (musicId 글자, ""), maimai는 (제목, STD/DX).
PlayCountKey = tuple[str, str]
# 곡 상세로 플레이 횟수를 받을 수 없는 난이도(위 `key_for` 설명).
_UNREACHABLE: dict[str, tuple[str, ...]] = {"maimai": ("UTA",), "chunithm": ("WE",)}


@dataclass(slots=True, kw_only=True, frozen=True)
class RecentPlay:
    """최근 플레이 목록의 한 판. `track`은 그 크레딧에서 몇 번째 곡인지(1부터)."""

    title: str
    chart_type: str
    played_at: datetime
    track: int | None
    difficulty: str = ""


@dataclass(slots=True, kw_only=True, frozen=True)
class PlayActivity:
    """수집 시작 때 NET에서 읽는 것: 총 플레이 횟수(크레딧)와 최근 플레이 목록."""

    total_plays: int | None
    recent: list[RecentPlay]


@dataclass(slots=True, kw_only=True, frozen=True)
class PlayCountState:
    total_plays: int | None
    newest_play_at: str | None
    complete: bool
    updated_at: str


@dataclass(slots=True, kw_only=True, frozen=True)
class CollectResult:
    mode: str  # "full" | "recent" | "none"
    asked: int
    fetched: int


class PlayCountNet(Protocol):
    """수집에 쓰는 NET 서비스 쪽 두 메서드. CHUNITHM은 곡을 musicId(int)로, maimai는 (제목, 종류)로 받는다."""

    async def play_activity(self, player_id: str, /, *, ctx: ToolContext | None = None) -> PlayActivity: ...
    async def play_counts(self, player_id: str, keys: Sequence[Any], /, *, ctx: ToolContext | None = None) -> Mapping[Any, dict[str, int]]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PlayCountService:
    """한 게임의 플레이 횟수 저장본. 스킬에는 `play_counts`라는 이름으로 들어간다(게임마다 그 게임 것이 낀다)."""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        game: str,
        records: RecordsService,
        songdb: SongDb,
        net: PlayCountNet | None,
        now: Callable[[], str] = _now,
    ) -> None:
        self._conn = conn
        self._game = game
        self._records = records
        self._songdb = songdb
        self._net = net
        self._now = now
        self._running: set[str] = set()

    # ------------------------------------------------------------ 읽기

    def key_for(self, title: str, chart_type: str, difficulty: str = "") -> PlayCountKey | None:
        """채보 표의 (곡 제목, 채보 종류) → 저장 키. CHUNITHM은 곡 DB에서 musicId를 찾는다(없으면 None).
        곡 상세로 받을 수 없는 채보는 None(모름)이다: maimai 宴은 곡 상세로 가는 목록에 없고, CHUNITHM WORLD'S END는 NET에서
        따로 된 곡이라 보통 곡 상세에 없다(곡 DB는 같은 곡 밑에 둔다 — 그대로 두면 0회로 잘못 나온다). 수집 대상에 넣으면
        수집이 늘 "못 읽은 곡 있음"이 되어 매번 전체를 다시 받는다."""
        if difficulty in _UNREACHABLE.get(self._game, ()):
            return None
        if self._game == "maimai":
            return (title, chart_type)
        song = self._songdb.song_by_title(title)
        return (str(song.id), "") if song is not None and song.id else None

    async def counts(self, player_id: str, keys: Sequence[PlayCountKey]) -> dict[PlayCountKey, dict[str, int]]:
        """저장된 곡만 돌려준다. 곡이 있는데 난이도가 없으면 0회, 곡이 없으면 모름(결과에 없음)."""
        wanted = list(dict.fromkeys(keys))
        found: dict[PlayCountKey, dict[str, int]] = {}
        for start in range(0, len(wanted), 400):  # SQLite 변수 개수 상한 안쪽으로 나눈다
            chunk = wanted[start:start + 400]
            where = " OR ".join("(song_key = ? AND chart_type = ?)" for _ in chunk)
            cursor = await self._conn.execute(
                f"SELECT song_key, chart_type, counts_json FROM play_counts WHERE player_id = ? AND game = ? AND ({where})",
                (player_id, self._game, *(part for key in chunk for part in key)),
            )
            for row in await cursor.fetchall():
                found[(row[0], row[1])] = json.loads(row[2])
        return found

    async def state(self, player_id: str) -> PlayCountState | None:
        cursor = await self._conn.execute(
            "SELECT total_plays, newest_play_at, complete, updated_at FROM play_count_state WHERE player_id = ? AND game = ?",
            (player_id, self._game),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return PlayCountState(total_plays=row[0], newest_play_at=row[1], complete=bool(row[2]), updated_at=row[3])

    def collecting(self, player_id: str) -> bool:
        return player_id in self._running

    # ------------------------------------------------------------ 수집

    async def collect(self, player_id: str, *, ctx: ToolContext | None = None) -> CollectResult | None:
        """동기화 뒤 배경에서 부른다. 같은 플레이어의 수집이 이미 돌고 있으면 건너뛴다(None)."""
        if self._net is None:
            raise ConnectionError("NET 서비스가 없다")
        if player_id in self._running:
            return None
        self._running.add(player_id)
        try:
            return await self._collect(self._net, player_id, ctx)
        finally:
            self._running.discard(player_id)

    async def _collect(self, net: PlayCountNet, player_id: str, ctx: ToolContext | None) -> CollectResult:
        activity = await net.play_activity(player_id, ctx=ctx)
        previous = await self.state(player_id)
        newest = max((play.played_at for play in activity.recent), default=None)
        newest_text = newest.isoformat() if newest is not None else (previous.newest_play_at if previous else None)

        targets: list[PlayCountKey] = []
        mode = "full"
        if previous is not None and previous.complete and previous.total_plays is not None and activity.total_plays is not None:
            added = activity.total_plays - previous.total_plays
            if added == 0:
                await self._save_state(player_id, activity.total_plays, newest_text, complete=True)
                return CollectResult(mode="none", asked=0, fetched=0)
            since = datetime.fromisoformat(previous.newest_play_at) if previous.newest_play_at else None
            new_plays = [play for play in activity.recent if since is None or play.played_at > since]
            credits_seen = sum(1 for play in new_plays if play.track == 1)
            if since is not None and added == credits_seen:
                mode = "recent"
                targets = [key for play in new_plays if (key := self.key_for(play.title, play.chart_type, play.difficulty)) is not None]
            else:
                logger.info(
                    "%s 플레이 횟수: 놓친 판이 있어 전체를 다시 받는다(총 플레이 %+d, 최근 목록에 새 크레딧 %d)",
                    self._game, added, credits_seen,
                )
        if mode == "full":
            targets = [key for row in await self._records.all_pb_rows(player_id)
                       if (key := self.key_for(row.song_title, row.chart_type, row.difficulty)) is not None]
        targets = list(dict.fromkeys(targets))

        fetched = await net.play_counts(player_id, self._net_keys(targets), ctx=ctx)
        stored = await self._store(player_id, fetched)
        complete = stored == len(targets)
        await self._save_state(player_id, activity.total_plays, newest_text, complete=complete)
        logger.info("%s 플레이 횟수 수집(%s): %d곡 중 %d곡", self._game, mode, len(targets), stored)
        return CollectResult(mode=mode, asked=len(targets), fetched=stored)

    def _net_keys(self, keys: Sequence[PlayCountKey]) -> list[Any]:
        return list(keys) if self._game == "maimai" else [int(song_key) for song_key, _ in keys]

    async def _store(self, player_id: str, fetched: Mapping[Any, dict[str, int]]) -> int:
        now = self._now()
        rows = [
            (player_id, self._game, *(key if isinstance(key, tuple) else (str(key), "")), json.dumps(counts), now)
            for key, counts in fetched.items()
        ]
        await self._conn.executemany(
            "INSERT INTO play_counts (player_id, game, song_key, chart_type, counts_json, fetched_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (player_id, game, song_key, chart_type) DO UPDATE SET counts_json = excluded.counts_json, "
            "fetched_at = excluded.fetched_at",
            rows,
        )
        await self._conn.commit()
        return len(rows)

    async def _save_state(self, player_id: str, total_plays: int | None, newest: str | None, *, complete: bool) -> None:
        await self._conn.execute(
            "INSERT INTO play_count_state (player_id, game, total_plays, newest_play_at, complete, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (player_id, game) DO UPDATE SET total_plays = excluded.total_plays, "
            "newest_play_at = excluded.newest_play_at, complete = excluded.complete, updated_at = excluded.updated_at",
            (player_id, self._game, total_plays, newest, int(complete), self._now()),
        )
        await self._conn.commit()
