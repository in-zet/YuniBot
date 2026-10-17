# services/chunithm/records.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import aiosqlite

from core.tool_types import ToolContext
from services.chunithm.records_calc import (
    allowed_attacks,
    assign_session_date,
    average_rating,
    candidate_upside,
    chart_rating,
    frame_changes,
    match_session_for_chart,
    remaining_to_target,
)
from services.chunithm.songdb import SongDbService
from services.common.players import PlayersService

if TYPE_CHECKING:
    from chuni_penguin.types import Difficulty, PersonalBest

    from services.chunithm.chunithm_net import SyncFetch

_GAME = "chunithm"
# records_pb.rating / records_sync_log.computed_rating은 TEXT NOT NULL이라 None을
# 그대로 넣을 수 없다 — 상수를 몰라 계산하지 못한 값은 이 표시로 남긴다.
_UNKNOWN_RATING = "계산 불가"
# 동기화 비교 데이터(diff)는 메모리에만 둔다(결정 사항 H). 한 요청 안에서 쓰고 버려지므로
# 이 이상 쌓일 일이 없다 — 넘치면 가장 오래된 것부터 지운다.
_DIFF_CACHE_LIMIT = 200
_BEST_FRAME_SIZE = 30
_NEW_FRAME_SIZE = 20
# 후보곡 상승폭·남은 점수의 기준점은 SSS다(결정 사항 K).
_SSS_SCORE = 1_007_500
_DIFFICULTY_CODES: tuple[str, ...] = ("BAS", "ADV", "EXP", "MAS", "ULT", "WE")

type ChartKey = tuple[str, str]


def _rating_str(value: Decimal | None) -> str:
    return str(value) if value is not None else _UNKNOWN_RATING


def _rating_from_str(value: str | None) -> Decimal | None:
    """_rating_str의 반대 방향. "계산 불가"(또는 숫자로 읽을 수 없는 값)는 None이다 —
    Decimal("계산 불가")를 그대로 만들면 InvalidOperation이 터진다."""
    if value is None or value == _UNKNOWN_RATING:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _difficulty_code(difficulty: Difficulty) -> str:
    return _SHORT_CODE.get(difficulty.name, difficulty.name)


@dataclass(slots=True, kw_only=True, frozen=True)
class SessionInfo:
    id: str
    play_date: str  # ISO 날짜
    estimated: bool


@dataclass(slots=True, kw_only=True, frozen=True)
class ChartUpdate:
    song_title: str
    difficulty: str
    old_score: int | None
    new_score: int
    old_rank: str | None
    new_rank: str
    old_rating: Decimal | None
    new_rating: Decimal | None
    session_id: str
    estimated_session: bool
    entered_frame: bool


@dataclass(slots=True, kw_only=True, frozen=True)
class UnchangedPlay:
    song_title: str
    difficulty: str
    play_count: int
    current_score: int
    session_id: str


@dataclass(slots=True, kw_only=True, frozen=True)
class FrameExit:
    song_title: str
    difficulty: str
    old_rating: Decimal | None


@dataclass(slots=True, kw_only=True, frozen=True)
class SyncDiff:
    sync_id: str
    sessions: list[SessionInfo]
    chart_updates: list[ChartUpdate]
    unchanged_plays: list[UnchangedPlay]
    frame_exits: list[FrameExit]
    previous_computed_rating: Decimal | None
    previous_best30_average: Decimal | None
    previous_new20_average: Decimal | None


@dataclass(slots=True, kw_only=True)
class SyncResult:
    sync_id: str
    is_first_sync: bool
    has_new_plays: bool
    # 상수를 모르는 채보가 하나라도 있으면 None("계산 불가")이다.
    computed_rating: Decimal | None
    best30_average: Decimal | None
    new20_average: Decimal | None
    displayed_rating: Decimal | None
    rating_mismatch: bool
    overpower_value: float | None
    overpower_percentage: float | None


@dataclass(slots=True, kw_only=True, frozen=True)
class PbRow:
    song_title: str
    difficulty: str
    score: int
    rank: str
    clear_lamp: str
    combo_lamp: str
    chain_lamp: str
    const: float | None
    rating: Decimal | None
    session_id: str | None
    updated_at: str


@dataclass(slots=True, kw_only=True, frozen=True)
class SyncLogSummary:
    nickname: str | None
    computed_rating: Decimal | None
    best30_average: Decimal | None  # 지금 시점 records_pb 기준(latest_sync_summary 참고)
    new20_average: Decimal | None  # 지금 시점 records_pb 기준
    last_synced_at: str


@dataclass(slots=True, kw_only=True, frozen=True)
class RatingDeltas:
    """최근 두 동기화 시점의 세 지표(records_sync_log 스냅샷). 변화량은 부르는 쪽이 뺀다."""

    computed_rating: Decimal | None
    best30_average: Decimal | None
    new20_average: Decimal | None
    has_previous: bool  # 직전 동기화가 있었나(없으면 previous_*는 전부 None)
    previous_computed_rating: Decimal | None
    previous_best30_average: Decimal | None
    previous_new20_average: Decimal | None


@dataclass(slots=True, kw_only=True, frozen=True)
class CandidateHit:
    song_title: str
    difficulty: str
    const: float
    upside: Decimal  # candidate_upside() 결과
    current_score: int  # 안 쳤으면 0
    remaining_score: int | None  # SSS 기준, 노트 수 없으면 None
    remaining_attacks: Decimal | None


def _frame_sort_key(row: PbRow) -> tuple[bool, Decimal, str, str]:
    # 레이팅 내림차순. 레이팅을 모르는 채보는 맨 뒤로, 동점은 제목·난이도 순으로 고정한다.
    return (row.rating is None, -(row.rating or Decimal(0)), row.song_title, row.difficulty)


def _frame_floor(frame: list[PbRow], size: int) -> Decimal:
    """프레임 최저 레이팅. 프레임이 아직 다 안 찼으면 어떤 채보든 그대로 들어가므로 0이다."""
    known = [r.rating for r in frame if r.rating is not None]
    if len(frame) < size or not known:
        return Decimal("0.00")
    return min(known)


def _known_min(ratings: list[Decimal | None]) -> Decimal | None:
    known = [r for r in ratings if r is not None]
    return min(known) if known else None


def _merge_personal_bests(fetch: SyncFetch) -> dict[ChartKey, PersonalBest]:
    """베스트30·신곡20·전체 PB를 (제목, 난이도)로 합친다. 같은 채보가 여러 목록에 있으면
    점수가 높은 쪽을 쓴다(정상이라면 같은 값이다)."""
    merged: dict[ChartKey, PersonalBest] = {}
    sources: list[PersonalBest] = [*fetch.best30, *fetch.new20]
    for code in _DIFFICULTY_CODES:
        sources.extend(fetch.all_personal_bests.get(code, []))
    for pb in sources:
        key = (pb.song.title, _difficulty_code(pb.chart.difficulty))
        existing = merged.get(key)
        if existing is None or pb.score > existing.score:
            merged[key] = pb
    return merged


def _as_aware(value: datetime) -> datetime:
    # 어댑터는 achieved_at을 Asia/Tokyo로 붙여 준다. 혹시 naive면 이 기계의 로컬 시각으로 본다.
    return value if value.tzinfo is not None else value.astimezone()


class RecordsService:
    """기록 스냅샷, 베스트, 세션 id, 레이팅 계산과 CHUNITHM-NET 대조."""

    def __init__(self, *, conn: aiosqlite.Connection, songdb: SongDbService, players: PlayersService) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row
        self._songdb = songdb
        self._players = players
        self._diff_cache: dict[str, SyncDiff] = {}

    async def save_sync(self, player_id: str, fetch: SyncFetch, ctx: ToolContext) -> SyncResult:
        """동기화 결과를 저장한다. 베스트30·신곡20·전체 PB를 records_pb에 합쳐 넣고
        (점수가 오른 채보만 행 전체를 바꾼다), 첫 동기화가 아니면 직전 상태와 비교한
        diff를 계산해 메모리 캐시에 둔다(sync_diff로 읽는다, 결정 사항 F/G/H).

        첫 동기화는 기준점만 만든다 — 세션도 diff도 만들지 않는다(리포트를 내지 않는다).
        세션 날짜는 동기화 시각이 아니라 최근 플레이 이력의 achieved_at(CHUNITHM-NET이
        붙여 주는 +09:00 벽시계)으로 정한다."""
        cursor = await self._conn.execute(
            "SELECT synced_at, computed_rating FROM records_sync_log WHERE player_id = ? AND game = ? "
            "ORDER BY synced_at DESC LIMIT 1",
            (player_id, _GAME),
        )
        previous_log = await cursor.fetchone()
        is_first_sync = previous_log is None

        sync_id = str(uuid.uuid4())
        synced_at = datetime.now(UTC)
        merged = _merge_personal_bests(fetch)

        # "베스트 평균"(통과 기준)은 베스트30과 신곡20을 각각 따로 평균한 값이다 — 둘을
        # 합쳐 50으로 나눈 전체 레이팅과는 다른 숫자다. 평균을 공식대로(30곡/20곡 가중)
        # 다시 합치면 이미 저장본으로 검산된 computed_rating과 정확히 같아야 한다.
        best30_ratings = [self._rating_of(pb) for pb in fetch.best30]
        new20_ratings = [self._rating_of(pb) for pb in fetch.new20]
        best30_average = average_rating(best30_ratings)
        new20_average = average_rating(new20_ratings)
        computed_rating = average_rating(best30_ratings + new20_ratings)

        displayed_rating = None
        if fetch.profile.rating_systems:
            displayed_rating = Decimal(str(fetch.profile.rating_systems[0].value))
        # 계산 불가(None)인데 표시값이 있으면 대조할 수 없으므로 불일치로 남긴다.
        rating_mismatch = displayed_rating is not None and computed_rating != displayed_rating

        over_power = fetch.profile.over_power
        overpower_value = over_power.value if over_power is not None else None
        overpower_percentage = over_power.percentage if over_power is not None else None

        diff: SyncDiff | None = None
        has_new_plays = False
        try:
            if previous_log is None:
                for pb in merged.values():
                    await self._upsert_pb(player_id, pb, session_id=None, synced_at=synced_at)
            else:
                diff, has_new_plays = await self._apply_with_diff(
                    player_id, fetch, merged, sync_id=sync_id, synced_at=synced_at,
                    previous_synced_at=datetime.fromisoformat(previous_log["synced_at"]),
                    previous_computed_rating=_rating_from_str(previous_log["computed_rating"]),
                )

            await self._conn.execute(
                "INSERT INTO records_sync_log (id, player_id, game, synced_at, nickname, "
                "displayed_rating, computed_rating, rating_mismatch, is_first_sync, "
                "overpower_value, overpower_percentage, best30_average_snapshot, new20_average_snapshot) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sync_id, player_id, _GAME, synced_at.isoformat(), fetch.profile.username,
                    str(displayed_rating) if displayed_rating is not None else None,
                    _rating_str(computed_rating), int(rating_mismatch), int(is_first_sync),
                    overpower_value, overpower_percentage,
                    # 첫 동기화도 남긴다 — 다음 동기화가 비교할 기준점이다.
                    _rating_str(best30_average), _rating_str(new20_average),
                ),
            )
            await self._conn.commit()
        except BaseException:
            # 부분 저장을 남기지 않는다 — PB·세션·로그는 한 단위로 들어가거나 하나도 안 들어간다.
            await self._conn.rollback()
            raise

        if is_first_sync:
            await self._players.set_status_tag(player_id, "chunithm-synced", source="records:first_sync")

        if diff is not None:
            self._diff_cache[sync_id] = diff
            while len(self._diff_cache) > _DIFF_CACHE_LIMIT:
                self._diff_cache.pop(next(iter(self._diff_cache)))

        return SyncResult(
            sync_id=sync_id,
            is_first_sync=is_first_sync,
            has_new_plays=has_new_plays,
            computed_rating=computed_rating,
            best30_average=best30_average,
            new20_average=new20_average,
            displayed_rating=displayed_rating,
            rating_mismatch=rating_mismatch,
            overpower_value=overpower_value,
            overpower_percentage=overpower_percentage,
        )

    async def _apply_with_diff(
        self,
        player_id: str,
        fetch: SyncFetch,
        merged: dict[ChartKey, PersonalBest],
        *,
        sync_id: str,
        synced_at: datetime,
        previous_synced_at: datetime,
        previous_computed_rating: Decimal | None,
    ) -> tuple[SyncDiff, bool]:
        """첫 동기화가 아닐 때: PB를 합쳐 넣으면서 직전 상태와의 비교 데이터를 만든다.
        (diff, 새 플레이가 있었나)를 돌려준다. 커밋은 부르는 쪽(save_sync)이 한다."""
        # 1) 직전 동기화 이후의 최근 플레이만 "이번에 새로 생긴 플레이"로 본다(결정 사항 F).
        recent_times: dict[ChartKey, list[datetime]] = {}
        recent_scores: dict[ChartKey, list[int]] = {}
        for recent in fetch.recent_scores:
            if recent.achieved_at is None:
                continue
            achieved_at = _as_aware(recent.achieved_at)
            if achieved_at <= previous_synced_at:
                continue
            key = (recent.song.title, _difficulty_code(recent.chart.difficulty))
            recent_times.setdefault(key, []).append(achieved_at)
            recent_scores.setdefault(key, []).append(recent.score)

        # 2) 새 플레이의 세션 날짜(새벽 4시 경계)마다 세션을 만든다(이미 있으면 그대로 쓴다).
        sessions: dict[date, SessionInfo] = {}
        for play_date in sorted({assign_session_date(t) for times in recent_times.values() for t in times}):
            sessions[play_date] = await self._get_or_create_session(player_id, play_date, estimated=False)
        sessions_by_date = {d: s.id for d, s in sessions.items()}
        latest_session_id = sessions[max(sessions)].id if sessions else None
        # match_session_for_chart는 achieved_at의 달력 날짜(.date())로 세션을 찾는다. 새벽 4시
        # 경계를 반영하려면 각 시각을 "그 플레이의 세션 날짜 자정"으로 바꿔 넘긴다 — 순서는
        # 그대로 보존되므로(경계 배정은 단조) "가장 늦은 플레이"도 그대로다.
        recent_by_session_day = {
            key: [datetime.combine(assign_session_date(t), time()) for t in times]
            for key, times in recent_times.items()
        }

        # 3) 동기화 적용 전 records_pb로 "이전 프레임"을 다시 계산한다(결정 사항 G).
        previous_rows = await self.all_pb_rows(player_id)
        previous_by_key = {(r.song_title, r.difficulty): r for r in previous_rows}
        previous_best, previous_new = self._frames(previous_rows)
        previous_frame = {(r.song_title, r.difficulty) for r in (*previous_best, *previous_new)}
        current_frame = {
            (pb.song.title, _difficulty_code(pb.chart.difficulty)) for pb in (*fetch.best30, *fetch.new20)
        }
        entered, exited = frame_changes(previous_frame, current_frame)

        # 4) 채보별로 갱신 여부를 판단해 저장하고 ChartUpdate를 모은다.
        chart_updates: list[ChartUpdate] = []
        improved_keys: set[ChartKey] = set()
        for key, pb in merged.items():
            existing = previous_by_key.get(key)
            if existing is not None and pb.score <= existing.score:
                continue
            improved_keys.add(key)
            # 처음 보는 채보(old_score=None)도 이미 있던 채보의 갱신과 똑같이 다룬다 — 최근 이력에서
            # 못 찾으면 가장 늦은 세션에 estimated로 붙는다(결정 사항 F).
            session_id, estimated = match_session_for_chart(
                key, recent_by_session_day, sessions_by_date, latest_session_id
            )
            if session_id is None:
                # 플레이 없이 PB가 오를 수는 없지만, 생기면 오늘(로컬) 날짜 세션을 추정으로 붙인다.
                today = assign_session_date(datetime.now())  # noqa: DTZ005 - 로컬 벽시계 기준이 맞다
                info = await self._get_or_create_session(player_id, today, estimated=True)
                sessions[today] = info
                sessions_by_date[today] = info.id
                latest_session_id = info.id
                session_id, estimated = info.id, True

            _, rating = await self._upsert_pb(player_id, pb, session_id=session_id, synced_at=synced_at)
            chart_updates.append(
                ChartUpdate(
                    song_title=key[0],
                    difficulty=key[1],
                    old_score=existing.score if existing is not None else None,
                    new_score=pb.score,
                    old_rank=existing.rank if existing is not None else None,
                    new_rank=str(pb.rank),
                    old_rating=existing.rating if existing is not None else None,
                    new_rating=rating,
                    session_id=session_id,
                    estimated_session=estimated,
                    entered_frame=key in entered,
                )
            )

        # 5) 플레이했지만 최고 기록을 못 넘긴 채보(goals.md의 "미갱신").
        unchanged_plays: list[UnchangedPlay] = []
        for key in sorted(recent_times):
            if key in improved_keys:
                continue
            known_scores = [
                *([previous_by_key[key].score] if key in previous_by_key else []),
                *([merged[key].score] if key in merged else []),
            ]
            session_id, _ = match_session_for_chart(key, recent_by_session_day, sessions_by_date, latest_session_id)
            assert session_id is not None  # 최근 이력에 있는 채보는 그 날짜 세션이 반드시 있다
            unchanged_plays.append(
                UnchangedPlay(
                    song_title=key[0],
                    difficulty=key[1],
                    play_count=len(recent_times[key]),
                    current_score=max(known_scores) if known_scores else max(recent_scores[key]),
                    session_id=session_id,
                )
            )

        # 이탈 오탐 거르기: "이전 프레임"은 records_pb를 다시 정렬해 만든 것이라, 레이팅이 같은
        # 채보 사이의 순서가 CHUNITHM-NET의 실제 동점 처리와 다를 수 있다. 그래서 저장된 레이팅이
        # 이번 프레임(CHUNITHM-NET이 준 베스트30/신곡20)의 최저 레이팅보다 엄격히 낮을 때만
        # 진짜 이탈로 본다. 같거나 높으면 정렬 차이로 생긴 오탐으로 보고 뺀다. 진입은
        # 이번 프레임을 그대로 믿으므로 손대지 않는다.
        best_floor = _known_min([self._rating_of(pb) for pb in fetch.best30])
        new_floor = _known_min([self._rating_of(pb) for pb in fetch.new20])
        frame_exits: list[FrameExit] = []
        for key in sorted(exited):
            old_rating = previous_by_key[key].rating
            floor = new_floor if self._songdb.is_new_for(key[0]) else best_floor
            if old_rating is not None and floor is not None and old_rating >= floor:
                continue
            frame_exits.append(FrameExit(song_title=key[0], difficulty=key[1], old_rating=old_rating))
        chart_updates.sort(
            key=lambda u: (u.new_rating is None, -(u.new_rating or Decimal(0)), u.song_title, u.difficulty)
        )
        diff = SyncDiff(
            sync_id=sync_id,
            sessions=[sessions[d] for d in sorted(sessions)],
            chart_updates=chart_updates,
            unchanged_plays=unchanged_plays,
            frame_exits=frame_exits,
            previous_computed_rating=previous_computed_rating,
            previous_best30_average=average_rating([r.rating for r in previous_best]),
            previous_new20_average=average_rating([r.rating for r in previous_new]),
        )
        # "새 플레이가 있었다"의 정의는 여기 한 곳뿐이다: 직전 동기화 이후 최근 이력에 새 항목이
        # 있거나, 최근 이력엔 아직 안 보여도 PB가 실제로 올랐다(크레딧 직후 동기화하면 최근
        # 이력 반영이 늦을 수 있다 — PB가 오른 것 자체가 플레이가 있었다는 증거다).
        return diff, bool(recent_times) or bool(chart_updates)

    async def sync_diff(self, sync_id: str) -> SyncDiff | None:
        """save_sync가 만든 비교 데이터. 첫 동기화였거나 캐시에서 밀려났으면(재시작 포함) None."""
        return self._diff_cache.get(sync_id)

    async def latest_overpower(self, player_id: str) -> tuple[float | None, float | None] | None:
        """최신 동기화 로그의 (오버파워 값, 퍼센트). CHUNITHM-NET 표시값 그대로다(결정 사항 L).
        동기화한 적이 없으면 None, 있지만 값이 없으면 (None, None)."""
        cursor = await self._conn.execute(
            "SELECT overpower_value, overpower_percentage FROM records_sync_log "
            "WHERE player_id = ? AND game = ? ORDER BY synced_at DESC LIMIT 1",
            (player_id, _GAME),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["overpower_value"], row["overpower_percentage"]

    async def latest_sync_summary(self, player_id: str) -> SyncLogSummary | None:
        """최신 동기화 로그의 닉네임·계산 레이팅·시각과, 베스트30/신곡20 평균.

        두 평균은 로그에 열을 늘리지 않고 **지금 시점** records_pb를 레이팅 내림차순으로
        다시 훑어 계산한다 — 그래서 "그 동기화 시점"이 아니라 "지금" 값이다(동기화 사이에
        records_pb가 바뀌는 길은 save_sync뿐이라 보통은 같다)."""
        cursor = await self._conn.execute(
            "SELECT nickname, computed_rating, synced_at FROM records_sync_log "
            "WHERE player_id = ? AND game = ? ORDER BY synced_at DESC LIMIT 1",
            (player_id, _GAME),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        best, new = self._frames(await self.all_pb_rows(player_id))
        return SyncLogSummary(
            nickname=row["nickname"],
            computed_rating=_rating_from_str(row["computed_rating"]),
            best30_average=average_rating([r.rating for r in best]),
            new20_average=average_rating([r.rating for r in new]),
            last_synced_at=row["synced_at"],
        )

    async def rating_deltas(self, player_id: str) -> RatingDeltas | None:
        """가장 최근 두 동기화 로그의 레이팅·베스트30 평균·신곡20 평균(동기화 시점 스냅샷).
        동기화한 적이 없으면 None. 한 번뿐이면 has_previous=False이고 previous_*는 None이다.
        "계산 불가"(와 스냅샷 열이 생기기 전 행의 NULL)는 None으로 돌려준다."""
        cursor = await self._conn.execute(
            "SELECT computed_rating, best30_average_snapshot, new20_average_snapshot FROM records_sync_log "
            "WHERE player_id = ? AND game = ? ORDER BY synced_at DESC LIMIT 2",
            (player_id, _GAME),
        )
        rows = list(await cursor.fetchall())
        if not rows:
            return None
        latest = rows[0]
        previous = rows[1] if len(rows) > 1 else None
        return RatingDeltas(
            computed_rating=_rating_from_str(latest["computed_rating"]),
            best30_average=_rating_from_str(latest["best30_average_snapshot"]),
            new20_average=_rating_from_str(latest["new20_average_snapshot"]),
            has_previous=previous is not None,
            previous_computed_rating=_rating_from_str(previous["computed_rating"]) if previous else None,
            previous_best30_average=_rating_from_str(previous["best30_average_snapshot"]) if previous else None,
            previous_new20_average=_rating_from_str(previous["new20_average_snapshot"]) if previous else None,
        )

    async def pb_row(self, player_id: str, *, song_title: str, difficulty: str) -> PbRow | None:
        cursor = await self._conn.execute(
            f"SELECT {_PB_COLUMNS} FROM records_pb "
            "WHERE player_id = ? AND game = ? AND song_title = ? AND difficulty = ?",
            (player_id, _GAME, song_title, difficulty),
        )
        row = await cursor.fetchone()
        return _pb_row_from(row) if row is not None else None

    async def all_pb_rows(self, player_id: str) -> list[PbRow]:
        """그 플레이어의 모든 PB(제목·난이도 순). 레이팅 순 정렬은 부르는 쪽이 한다."""
        cursor = await self._conn.execute(
            f"SELECT {_PB_COLUMNS} FROM records_pb "
            "WHERE player_id = ? AND game = ? ORDER BY song_title, difficulty",
            (player_id, _GAME),
        )
        return [_pb_row_from(row) for row in await cursor.fetchall()]

    async def current_frames(self, player_id: str) -> tuple[list[PbRow], list[PbRow]]:
        """지금 시점 (베스트30, 신곡20). 제목 순이 아니라 프레임 순(레이팅 내림차순)이다 —
        latest_sync_summary/candidates가 쓰는 _frames와 같은 계산이라 숫자가 어긋나지 않는다."""
        return self._frames(await self.all_pb_rows(player_id))

    async def candidates(
        self, player_id: str, *, min_const: float | None, max_const: float | None, count: int
    ) -> list[CandidateHit]:
        """레이팅을 올릴 후보곡. 상수 범위 안에서 지금 프레임(베스트30/신곡20, records_pb
        기준) 밖에 있는 채보를, SSS를 냈을 때 그 채보가 속할 프레임 최저점을 얼마나 넘는지
        (candidate_upside, 결정 사항 K) 내림차순으로 count개까지 돌려준다. 상승폭이 0 이하인
        채보는 뺀다. 안 친 채보는 현재 점수 0으로 본다."""
        rows = await self.all_pb_rows(player_id)
        best, new = self._frames(rows)
        frame_keys = {(r.song_title, r.difficulty) for r in (*best, *new)}
        best_floor = _frame_floor(best, _BEST_FRAME_SIZE)
        new_floor = _frame_floor(new, _NEW_FRAME_SIZE)
        rows_by_key = {(r.song_title, r.difficulty): r for r in rows}
        low = min_const if min_const is not None else 0.0
        high = max_const if max_const is not None else 99.0

        hits: list[CandidateHit] = []
        for code in _DIFFICULTY_CODES:
            for hit in self._songdb.find_by_const(min_const=low, max_const=high, difficulty=code):
                key = (hit.song.title, code)
                const = hit.chart.const
                if key in frame_keys or const is None:
                    continue
                floor = new_floor if self._songdb.is_new_for(hit.song.title) else best_floor
                upside = candidate_upside(const, floor)
                if upside <= 0:
                    continue
                current_score = rows_by_key[key].score if key in rows_by_key else 0
                remaining = remaining_to_target(hit.chart.note_count, current_score, _SSS_SCORE)
                hits.append(
                    CandidateHit(
                        song_title=hit.song.title,
                        difficulty=code,
                        const=const,
                        upside=upside,
                        current_score=current_score,
                        remaining_score=remaining[0] if remaining is not None else None,
                        remaining_attacks=remaining[1] if remaining is not None else None,
                    )
                )
        hits.sort(key=lambda h: (-h.upside, -h.const, h.song_title, h.difficulty))
        return hits[:count]

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

    def _rating_of(self, pb: PersonalBest) -> Decimal | None:
        return chart_rating(pb.score, self._songdb.const_for(pb.song.title, _difficulty_code(pb.chart.difficulty)))

    def _frames(self, rows: list[PbRow]) -> tuple[list[PbRow], list[PbRow]]:
        """records_pb 행들로 (베스트30, 신곡20) 프레임을 다시 계산한다. 신곡 여부는
        songdb.is_new_for로 판정한다(결정 사항 G)."""
        best: list[PbRow] = []
        new: list[PbRow] = []
        for row in sorted(rows, key=_frame_sort_key):
            if self._songdb.is_new_for(row.song_title):
                if len(new) < _NEW_FRAME_SIZE:
                    new.append(row)
            elif len(best) < _BEST_FRAME_SIZE:
                best.append(row)
        return best, new

    async def _upsert_pb(
        self, player_id: str, pb: PersonalBest, *, session_id: str | None, synced_at: datetime
    ) -> tuple[float | None, Decimal | None]:
        """PB 한 행을 넣는다. 점수가 실제로 올랐을 때만 행 전체(점수·랭크·램프·상수·레이팅·
        세션·시각)를 한 단위로 바꾼다 — "이 기록을 언제 세웠나"(session_id/updated_at)는
        세션·리포트 스킬이 쓰는 정보다. (상수, 레이팅)을 돌려준다."""
        title = pb.song.title
        difficulty = _difficulty_code(pb.chart.difficulty)
        const = self._songdb.const_for(title, difficulty)
        rating = chart_rating(pb.score, const)
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
                player_id, _GAME, title, difficulty, pb.score, str(pb.rank),
                str(pb.clear_lamp), str(pb.combo_lamp), str(pb.chain_lamp),
                const, _rating_str(rating), session_id, synced_at.isoformat(),
            ),
        )
        return const, rating

    async def _get_or_create_session(self, player_id: str, play_date: date, *, estimated: bool) -> SessionInfo:
        cursor = await self._conn.execute(
            "SELECT id, estimated FROM records_sessions WHERE player_id = ? AND game = ? AND play_date = ?",
            (player_id, _GAME, play_date.isoformat()),
        )
        row = await cursor.fetchone()
        if row is not None:
            return SessionInfo(id=row["id"], play_date=play_date.isoformat(), estimated=bool(row["estimated"]))

        session_id = str(uuid.uuid4())
        await self._conn.execute(
            "INSERT INTO records_sessions (id, player_id, game, play_date, estimated, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, player_id, _GAME, play_date.isoformat(), int(estimated), datetime.now(UTC).isoformat()),
        )
        return SessionInfo(id=session_id, play_date=play_date.isoformat(), estimated=estimated)


_PB_COLUMNS = (
    "song_title, difficulty, score, rank, clear_lamp, combo_lamp, chain_lamp, const, rating, "
    "session_id, updated_at"
)


def _pb_row_from(row: aiosqlite.Row) -> PbRow:
    return PbRow(
        song_title=row["song_title"],
        difficulty=row["difficulty"],
        score=row["score"],
        rank=row["rank"],
        clear_lamp=row["clear_lamp"],
        combo_lamp=row["combo_lamp"],
        chain_lamp=row["chain_lamp"],
        const=row["const"],
        rating=_rating_from_str(row["rating"]),
        session_id=row["session_id"],
        updated_at=row["updated_at"],
    )


_SHORT_CODE = {
    "basic": "BAS", "advanced": "ADV", "expert": "EXP",
    "master": "MAS", "ultima": "ULT", "worlds_end": "WE",
}
