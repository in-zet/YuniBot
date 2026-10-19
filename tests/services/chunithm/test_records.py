# tests/services/chunithm/test_records.py
import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import pytest
from chuni_penguin.adapters.chunithm_net.parser import (
    parse_music_for_rating,
    parse_player_data,
)
from chuni_penguin.types import Chart, Difficulty, PersonalBest, Song
from chuni_penguin.types.lamp import ClearLamp, ComboLamp
from chuni_penguin.types.score import Rank, RecentScore
from selectolax.lexbor import LexborHTMLParser

from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm import records as records_module
from services.chunithm.chunithm_net import SyncFetch
from services.chunithm.records import (
    FrameExit,
    PbRow,
    RecordsService,
    SessionInfo,
    UnchangedPlay,
)
from services.chunithm.records_calc import (
    allowed_attacks,
    assign_session_date,
    average_rating,
    candidate_upside,
    chart_rating,
    remaining_to_target,
)
from services.chunithm.songdb import SongDbService
from services.common.players import PlayersService

_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
_RECORDS_MIGRATION = (
    Path("migrations/core/008_records.sql").read_text()
    + Path("migrations/core/013_records_overpower.sql").read_text()
    + Path("migrations/core/014_records_sync_log_snapshot_averages.sql").read_text()
)
_JST = ZoneInfo("Asia/Tokyo")

_PROFILE_HTML = Path("docs/reference/chunithm-net/player_data.html").read_text(encoding="utf-8")
_BEST_HTML = Path("docs/reference/chunithm-net/rating_detail_best.html").read_text(encoding="utf-8")
_RECENT_HTML = Path("docs/reference/chunithm-net/rating_detail_recent.html").read_text(encoding="utf-8")


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_PLAYERS_MIGRATION + _RECORDS_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


def _fixture_fetch() -> SyncFetch:
    profile = parse_player_data(LexborHTMLParser(_PROFILE_HTML))
    best30 = parse_music_for_rating(LexborHTMLParser(_BEST_HTML))
    new20 = parse_music_for_rating(LexborHTMLParser(_RECENT_HTML))
    return SyncFetch(profile=profile, best30=best30, new20=new20, recent_scores=[])


async def test_first_sync_sets_baseline_and_grants_synced_tag(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.is_first_sync is True
    assert await players.tags_for(player.id) == {"chunithm-synced"}


async def test_second_sync_is_not_first(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)
    await service.save_sync(player.id, _fixture_fetch(), ctx)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.is_first_sync is False


async def test_save_sync_upserts_personal_bests(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM records_pb WHERE player_id = ?", (player.id,)
    )
    row = await cursor.fetchone()
    assert row[0] == 50  # 베스트30 + 신곡20, 중복 없음(제목이 전부 다른 저장본 기준)


async def test_save_sync_logs_sync_entry(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM records_sync_log WHERE player_id = ?", (player.id,)
    )
    row = await cursor.fetchone()
    assert row[0] == 1


async def test_computed_rating_matches_fixture_displayed_rating(conn) -> None:
    """이번 단계 전체의 핵심 통과 기준: 저장본 베스트30+신곡20으로 계산한 전체
    레이팅과 베스트 평균이 저장본의 표시값과 맞아야 한다. 두 저장본이 같은
    시점(같은 로그인 세션)에서 받은 것이어야 이 테스트가 의미 있다 — 이 계획을
    쓰던 중 실제로 세 파일 중 하나가 다른 시점이라 어긋난 적이 있다(player_data.html을
    베스트30/신곡20과 같은 시점으로 다시 저장해 고쳤다).

    전체 레이팅은 CHUNITHM-NET이 숫자 이미지로 보여주는 실제 표시값(16.94, 둘째 자리까지만
    보여준다)과 둘째 자리에서 대조한다 — 우리가 들고 있는 계산값(16.9480, 넷째 자리까지
    정확히 끝난다)은 표시값보다 정밀하다(사용자 확인 2026-10-18, `records_calc.average_rating`
    참고). 베스트30 평균(17.0216)·신곡20 평균(16.8375)도 같은 넷째 자리 버림이다(사용자 확인
    2026-10-18 — 세 지표를 같은 자리수로 통일). CHUNITHM-NET이 텍스트로 따로 보여주는
    페이지가 없어 저장본과 직접 대조할 수는 없지만, 공식대로(30곡·20곡 가중) 다시 합쳐 둘째
    자리로 버리면 표시 레이팅과 같아야 한다 — 이 일관성이 "베스트 평균"이 올바로 계산됐다는
    증거다. 아래 숫자는 지금 저장된 두 파일(2026-10-11)로 실제로 계산해 확인한 값이다 —
    저장본을 다시 받으면 이 상수들도 다시 계산해서 맞춰야 한다."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.displayed_rating is not None
    assert result.rating_mismatch is False, (
        f"계산값 {result.computed_rating} != 표시값 {result.displayed_rating}"
    )
    assert result.computed_rating == Decimal("16.9480")
    assert result.best30_average == Decimal("17.0216")
    assert result.new20_average == Decimal("16.8375")

    # 베스트 평균 일관성: 30곡·20곡 가중 재합산을 둘째 자리로 버리면 표시 레이팅과 같아야 한다.
    assert result.best30_average is not None and result.new20_average is not None
    recombined = ((result.best30_average * 30 + result.new20_average * 20) / 50).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    assert recombined == result.displayed_rating


async def test_second_sync_with_unimproved_score_does_not_overwrite_session_or_timestamp(
    conn, monkeypatch
) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)
    first_rows = await _pb_rows(conn, player.id)

    # 두 번째 동기화 — 다음 날, 같은 저장본이라 점수가 그대로다(개선 없음).
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 11, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 11, 21, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)
    second_rows = await _pb_rows(conn, player.id)

    assert len(first_rows) == 50
    assert first_rows == second_rows


async def test_improved_score_updates_row_as_a_unit(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)
    await service.save_sync(player.id, _fixture_fetch(), ctx)
    target = _fixture_fetch().best30[0]
    # 기존 기록을 일부러 낮춰 두고 다시 동기화하면 저장본 점수로 올라가야 한다.
    await conn.execute(
        "UPDATE records_pb SET score = 1, rating = 'old', session_id = 'old', updated_at = 'old' "
        "WHERE player_id = ? AND song_title = ?",
        (player.id, target.song.title),
    )
    await conn.commit()

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute(
        "SELECT score, rating, session_id, updated_at FROM records_pb WHERE player_id = ? AND song_title = ?",
        (player.id, target.song.title),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["score"] == target.score
    assert "old" not in (row["rating"], row["session_id"], row["updated_at"])


async def test_session_date_uses_local_wall_clock_not_utc(conn, monkeypatch) -> None:
    """세션 날짜는 동기화 시각(UTC)이 아니라 그 플레이의 achieved_at(CHUNITHM-NET이 주는
    일본/한국 벽시계, +09:00)으로 정한다. UTC로는 아직 10일 20시지만 벽시계로는 11일
    오전 5시(경계 4시 이후)인 플레이는 11일 세션이어야 한다 — UTC 날짜를 쓰면 10일로
    잘못 배정된다.

    첫 동기화는 기준점만 만들고 세션을 만들지 않으므로,
    둘째 동기화의 최근 플레이 이력으로 확인한다."""
    _, player_id, ctx, service = await _setup(conn)
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))
    await service.save_sync(player_id, _fixture_fetch(), ctx)

    target = _fixture_fetch().best30[4]
    achieved_at = datetime(2026, 10, 11, 5, 0, tzinfo=_JST)  # = 2026-10-10T20:00Z
    fetch = _fixture_fetch()
    fetch.recent_scores = [_recent(target.song.title, "MAS", target.score - 1000, achieved_at)]
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 21, 0, tzinfo=UTC), local=datetime(2026, 10, 11, 6, 0, tzinfo=UTC))
    result = await service.save_sync(player_id, fetch, ctx)

    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert [s.play_date for s in diff.sessions] == [assign_session_date(achieved_at).isoformat()]
    assert diff.sessions[0].play_date == "2026-10-11"
    assert diff.unchanged_plays[0].session_id == diff.sessions[0].id
    cursor = await conn.execute("SELECT play_date FROM records_sessions WHERE player_id = ?", (player_id,))
    assert [r[0] for r in await cursor.fetchall()] == ["2026-10-11"]
    cursor = await conn.execute(
        "SELECT synced_at FROM records_sync_log WHERE player_id = ? ORDER BY synced_at DESC LIMIT 1", (player_id,)
    )
    row = await cursor.fetchone()
    assert row is not None and row[0] == "2026-10-10T21:00:00+00:00"  # 저장 시각은 UTC 그대로


async def test_unknown_const_surfaces_as_not_computable(conn, monkeypatch) -> None:
    """상수를 모르는 채보가 하나라도 있으면 레이팅을 0으로 셈하지 않고 "계산 불가"로 남긴다."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    songdb = SongDbService()
    fetch = _fixture_fetch()
    unknown_title = fetch.new20[0].song.title
    real_const_for = songdb.const_for
    monkeypatch.setattr(
        songdb, "const_for",
        lambda title, difficulty: None if title == unknown_title else real_const_for(title, difficulty),
    )
    service = RecordsService(conn=conn, songdb=songdb, players=players)

    result = await service.save_sync(player.id, fetch, ctx)

    assert result.computed_rating is None
    assert result.new20_average is None
    assert result.best30_average is not None
    assert result.rating_mismatch is True  # 대조할 수 없다
    cursor = await conn.execute(
        "SELECT rating FROM records_pb WHERE player_id = ? AND song_title = ?", (player.id, unknown_title)
    )
    row = await cursor.fetchone()
    assert row is not None and row[0] == "계산 불가"
    cursor = await conn.execute("SELECT computed_rating FROM records_sync_log WHERE player_id = ?", (player.id,))
    row = await cursor.fetchone()
    assert row is not None and row[0] == "계산 불가"


async def test_best_score_returns_saved_pb(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)
    fetch = _fixture_fetch()
    await service.save_sync(player.id, fetch, ctx)
    target = fetch.best30[0]

    score = await service.best_score(player.id, song_title=target.song.title, difficulty="MAS")

    assert score == target.score


async def test_best_score_returns_none_when_never_played(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    score = await service.best_score(player.id, song_title="존재하지않는곡", difficulty="MAS")

    assert score is None


async def test_last_sync_at_returns_most_recent(conn, monkeypatch) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    assert await service.last_sync_at(player.id) is None

    # 두 번 동기화해 ORDER BY synced_at DESC가 실제로 골라야 하는 상황을 만든다.
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 11, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 11, 21, 0, tzinfo=UTC))
    await service.save_sync(player.id, _fixture_fetch(), ctx)

    # 첫 번째(10일)가 아니라 나중(11일) 동기화 시각이어야 한다.
    assert await service.last_sync_at(player.id) == "2026-10-11T12:00:00+00:00"


async def test_latest_session_returns_none_before_any_sync(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    assert await service.latest_session(player.id) is None


async def test_latest_session_returns_the_most_recent_play_date(conn, monkeypatch) -> None:
    """세션은 recent_scores의 achieved_at으로 만들어진다 — _fixture_fetch()만 반복해서는
    (recent_scores가 비어 있어) 생기지 않으므로, 실제 플레이가 있는 동기화 두 번으로 만든다."""
    _, player_id, ctx, service = await _setup(conn)
    target = _fixture_fetch().best30[4]

    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 12, 0, tzinfo=UTC), local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))
    await service.save_sync(player_id, _fixture_fetch(), ctx)

    first = _fixture_fetch()
    first.recent_scores = [_recent(target.song.title, "MAS", target.score - 1000, datetime(2026, 10, 10, 21, 0, tzinfo=_JST))]
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 10, 13, 0, tzinfo=UTC), local=datetime(2026, 10, 10, 22, 0, tzinfo=UTC))
    await service.save_sync(player_id, first, ctx)

    second = _fixture_fetch()
    second.recent_scores = [_recent(target.song.title, "MAS", target.score - 500, datetime(2026, 10, 11, 21, 0, tzinfo=_JST))]
    _freeze_now(monkeypatch, utc=datetime(2026, 10, 11, 13, 0, tzinfo=UTC), local=datetime(2026, 10, 11, 22, 0, tzinfo=UTC))
    await service.save_sync(player_id, second, ctx)

    latest = await service.latest_session(player_id)
    assert latest is not None
    assert latest.play_date == "2026-10-11"


async def test_allowed_attacks_and_remaining_to_target_are_exposed_passthroughs(conn) -> None:
    players = PlayersService(conn)
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    # 같은 순수 함수에 같은 인자를 넘긴 결과와 정확히 같아야 한다(인자 순서 포함).
    assert service.allowed_attacks(3000, 1_007_500) == allowed_attacks(3000, 1_007_500)
    assert service.remaining_to_target(3000, 1_000_000, 1_007_500) == remaining_to_target(
        3000, 1_000_000, 1_007_500
    )
    assert service.allowed_attacks(None, 1_007_500) is None
    assert service.remaining_to_target(None, 1_000_000, 1_007_500) is None

    # 기대값도 손으로 계산해 고정한다: (1,010,000 - 1,007,500) * 3000 / 510,000 = 14.70...
    assert service.allowed_attacks(3000, 1_007_500) == Decimal("14.70")
    # 남은 점수 7,500, 어택 7,500 * 3000 / 510,000 = 44.11...
    assert service.remaining_to_target(3000, 1_000_000, 1_007_500) == (7500, Decimal("44.11"))


def _freeze_now(monkeypatch: pytest.MonkeyPatch, *, utc: datetime, local: datetime) -> None:
    """records 모듈의 datetime.now를 고정한다. now(UTC)는 utc를, 인자 없는 now()는
    local의 벽시계 값(naive)을 돌려준다 — local은 tzinfo만 떼어 쓴다(벽시계 숫자를
    그대로 적으려는 것이지 UTC라는 뜻이 아니다)."""
    local_naive = local.replace(tzinfo=None)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return utc if tz is not None else local_naive

    monkeypatch.setattr(records_module, "datetime", _FrozenDatetime)


async def _pb_rows(conn: aiosqlite.Connection, player_id: str) -> list[tuple[object, ...]]:
    cursor = await conn.execute(
        "SELECT song_title, difficulty, score, rating, session_id, updated_at FROM records_pb "
        "WHERE player_id = ? ORDER BY song_title, difficulty",
        (player_id,),
    )
    return [tuple(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# 전체 PB 저장, 동기화 비교 데이터(diff), diff 캐시
# ---------------------------------------------------------------------------

_T1_UTC = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)  # 첫 동기화 시각(= 10일 21:00 JST)
_T2_UTC = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)  # 둘째 동기화 시각


async def _setup(
    conn: aiosqlite.Connection, *, songdb: SongDbService | None = None
) -> tuple[PlayersService, str, ToolContext, RecordsService]:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=songdb or SongDbService(), players=players)
    return players, player.id, ctx, service


def _songdb_with_fixture_new20(monkeypatch: pytest.MonkeyPatch) -> SongDbService:
    """저장본 신곡20 곡들만 신곡으로 보는 songdb. Chunimaru 덧씌우기 없이도 "이전
    프레임"(records_pb에서 다시 계산한 베스트30/신곡20)이 저장본과 같아지게 한다."""
    songdb = SongDbService()
    new_titles = {pb.song.title for pb in _fixture_fetch().new20}
    monkeypatch.setattr(songdb, "is_new_for", lambda title: title in new_titles)
    return songdb


def _recent(title: str, difficulty: str, score: int, achieved_at: datetime) -> RecentScore:
    return RecentScore(
        song=Song(id=0, title=title),
        chart=Chart(difficulty=Difficulty(difficulty)),
        score=score,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.none,
        achieved_at=achieved_at,
    )


def _personal_best(title: str, difficulty: str, score: int) -> PersonalBest:
    return PersonalBest(
        song=Song(id=0, title=title),
        chart=Chart(difficulty=Difficulty(difficulty)),
        score=score,
        clear_lamp=ClearLamp.clear,
        combo_lamp=ComboLamp.none,
    )


def _with_score(pb: PersonalBest, score: int) -> PersonalBest:
    return dataclasses.replace(pb, score=score, rank=Rank.from_score(score))


def _replace_in(records: list[PersonalBest], title: str, score: int) -> list[PersonalBest]:
    return [_with_score(pb, score) if pb.song.title == title else pb for pb in records]


async def _baseline(service: RecordsService, player_id: str, ctx: ToolContext, monkeypatch) -> None:
    _freeze_now(monkeypatch, utc=_T1_UTC, local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))
    await service.save_sync(player_id, _fixture_fetch(), ctx)
    _freeze_now(monkeypatch, utc=_T2_UTC, local=datetime(2026, 10, 13, 21, 0, tzinfo=UTC))


async def test_first_sync_saves_baseline_without_sessions(conn) -> None:
    _, player_id, ctx, service = await _setup(conn)

    result = await service.save_sync(player_id, _fixture_fetch(), ctx)

    assert result.is_first_sync is True
    assert result.has_new_plays is False
    assert await service.sync_diff(result.sync_id) is None  # 첫 동기화는 리포트를 안 내므로 diff도 없다
    cursor = await conn.execute("SELECT COUNT(*) FROM records_sessions WHERE player_id = ?", (player_id,))
    row = await cursor.fetchone()
    assert row is not None and row[0] == 0
    cursor = await conn.execute("SELECT id FROM records_sync_log WHERE player_id = ?", (player_id,))
    row = await cursor.fetchone()
    assert row is not None and row[0] == result.sync_id


async def test_second_sync_with_no_recent_plays_reports_no_new_plays(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn)
    await _baseline(service, player_id, ctx, monkeypatch)
    fetch = _fixture_fetch()
    # 최근 이력은 있지만 전부 직전 동기화(10일 12:00Z = 21:00 JST) 이전의 플레이다.
    target = fetch.best30[0]
    fetch.recent_scores = [_recent(target.song.title, "MAS", target.score, datetime(2026, 10, 10, 20, 0, tzinfo=_JST))]

    result = await service.save_sync(player_id, fetch, ctx)

    assert result.is_first_sync is False
    assert result.has_new_plays is False
    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert diff.sync_id == result.sync_id
    assert diff.chart_updates == []
    assert diff.sessions == []
    assert diff.unchanged_plays == []
    assert diff.frame_exits == []


async def test_improved_pb_matched_in_recent_history_uses_play_date(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn)
    await _baseline(service, player_id, ctx, monkeypatch)
    target = next(pb for pb in _fixture_fetch().best30 if pb.song.title == "I Wanna")
    new_score = 1_008_500
    fetch = _fixture_fetch()
    fetch.best30 = _replace_in(fetch.best30, "I Wanna", new_score)
    # 12일 01:30(벽시계)은 새벽 4시 경계 전이라 11일 세션이다.
    achieved_at = datetime(2026, 10, 12, 1, 30, tzinfo=_JST)
    fetch.recent_scores = [_recent("I Wanna", "MAS", new_score, achieved_at)]

    result = await service.save_sync(player_id, fetch, ctx)

    assert result.has_new_plays is True
    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert len(diff.sessions) == 1
    session = diff.sessions[0]
    assert session == SessionInfo(id=session.id, play_date="2026-10-11", estimated=False)
    assert len(diff.chart_updates) == 1
    update = diff.chart_updates[0]
    assert (update.song_title, update.difficulty) == ("I Wanna", "MAS")
    assert (update.old_score, update.new_score) == (target.score, new_score)
    assert (update.old_rank, update.new_rank) == ("SS+", "SSS")
    assert update.old_rating == chart_rating(target.score, 15.2)
    assert update.new_rating == chart_rating(new_score, 15.2)
    assert update.session_id == session.id
    assert update.estimated_session is False
    assert update.entered_frame is False  # 원래 베스트30 안에 있던 채보다
    pb = await service.pb_row(player_id, song_title="I Wanna", difficulty="MAS")
    assert pb is not None and pb.score == new_score and pb.session_id == session.id


async def test_improved_pb_missing_from_recent_history_is_estimated(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn)
    await _baseline(service, player_id, ctx, monkeypatch)
    fetch = _fixture_fetch()
    fetch.best30 = _replace_in(fetch.best30, "I Wanna", 1_008_500)  # 최근 이력에 있음
    fetch.best30 = _replace_in(fetch.best30, "Odin", 1_008_000)  # 최근 이력에 없음(보관 한도 밖)
    other = fetch.best30[0]
    fetch.recent_scores = [
        _recent(other.song.title, "MAS", other.score - 5000, datetime(2026, 10, 12, 21, 0, tzinfo=_JST)),
        _recent("I Wanna", "MAS", 1_008_500, datetime(2026, 10, 11, 20, 0, tzinfo=_JST)),
    ]

    result = await service.save_sync(player_id, fetch, ctx)

    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert [s.play_date for s in diff.sessions] == ["2026-10-11", "2026-10-12"]
    by_title = {u.song_title: u for u in diff.chart_updates}
    assert set(by_title) == {"I Wanna", "Odin"}
    assert by_title["I Wanna"].session_id == diff.sessions[0].id
    assert by_title["I Wanna"].estimated_session is False
    # 못 찾은 갱신은 이번 동기화에서 만든 세션 중 가장 늦은 날짜에 붙는다.
    assert by_title["Odin"].session_id == diff.sessions[1].id
    assert by_title["Odin"].estimated_session is True


async def test_improved_pb_without_any_new_play_gets_estimated_today_session(conn, monkeypatch) -> None:
    """플레이 없이 PB가 오르는 일은 이론상 없지만, 생기면 오늘(로컬) 날짜 세션을
    estimated로 만들어 붙인다."""
    _, player_id, ctx, service = await _setup(conn)
    await _baseline(service, player_id, ctx, monkeypatch)
    fetch = _fixture_fetch()
    fetch.best30 = _replace_in(fetch.best30, "Odin", 1_008_000)

    result = await service.save_sync(player_id, fetch, ctx)

    # PB가 올랐으면 최근 이력이 비어 있어도 새 플레이가 있었던 것이다(리포트가 "변화 없음"으로 숨지 않게).
    assert result.has_new_plays is True
    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert diff.sessions == [SessionInfo(id=diff.sessions[0].id, play_date="2026-10-13", estimated=True)]
    assert diff.chart_updates[0].session_id == diff.sessions[0].id
    assert diff.chart_updates[0].estimated_session is True


async def test_improved_pb_not_yet_in_recent_history_counts_as_new_play(conn, monkeypatch) -> None:
    """크레딧 직후 동기화: 최근 이력에는 직전 동기화 이전 플레이만 보이고(이번 크레딧은 아직
    반영 전) PB는 올랐다. 이때도 has_new_plays=True여야 리포트가 갱신을 보여 준다."""
    _, player_id, ctx, service = await _setup(conn)
    await _baseline(service, player_id, ctx, monkeypatch)
    target = next(pb for pb in _fixture_fetch().best30 if pb.song.title == "Odin")
    fetch = _fixture_fetch()
    fetch.best30 = _replace_in(fetch.best30, "Odin", 1_008_000)
    # 직전 동기화(10일 21:00 JST)보다 이른 플레이뿐이다 — "새 최근 플레이"는 0건.
    fetch.recent_scores = [_recent("Odin", "MAS", target.score, datetime(2026, 10, 10, 20, 0, tzinfo=_JST))]

    result = await service.save_sync(player_id, fetch, ctx)

    assert result.has_new_plays is True
    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert [(u.song_title, u.new_score) for u in diff.chart_updates] == [("Odin", 1_008_000)]
    assert diff.chart_updates[0].estimated_session is True
    assert diff.unchanged_plays == []


async def test_played_but_not_improved_goes_to_unchanged_plays(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn)
    await _baseline(service, player_id, ctx, monkeypatch)
    target = next(pb for pb in _fixture_fetch().best30 if pb.song.title == "Odin")
    fetch = _fixture_fetch()
    fetch.recent_scores = [
        _recent("Odin", "MAS", target.score - 3000, datetime(2026, 10, 12, 20, 0, tzinfo=_JST)),
        _recent("Odin", "MAS", target.score - 2000, datetime(2026, 10, 12, 20, 10, tzinfo=_JST)),
        _recent("Odin", "MAS", target.score - 1000, datetime(2026, 10, 12, 20, 20, tzinfo=_JST)),
    ]

    result = await service.save_sync(player_id, fetch, ctx)

    assert result.has_new_plays is True
    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert diff.chart_updates == []
    assert diff.unchanged_plays == [
        UnchangedPlay(
            song_title="Odin", difficulty="MAS", play_count=3, current_score=target.score,
            session_id=diff.sessions[0].id,
        )
    ]


_PLANT_SQL = (
    "INSERT INTO records_pb (player_id, game, song_title, difficulty, score, rank, clear_lamp, "
    "combo_lamp, chain_lamp, const, rating, session_id, updated_at) "
    "VALUES (?, 'chunithm', ?, 'MAS', 1000000, 'SS', 'CLEAR', 'NONE', 'NONE', 14.8, ?, NULL, 'x')"
)
# 저장본 베스트30에서 레이팅이 16.94 이하인 여섯 채보(16.94 둘, 16.93, 16.92, 16.91 둘).
_LOW_BEST30 = ("Igallta", "初音天地開闢神話", "ホーリーサンバランド", "Kaleidoscope", "Tempestissimo", "幻想ロードオブキング")


async def test_frame_entry_and_exit(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb=_songdb_with_fixture_new20(monkeypatch))
    await _baseline(service, player_id, ctx, monkeypatch)
    # 동기화 전 records_pb에 레이팅 16.94 채보를 심어 둔다 — "이전 프레임"(records_pb 재계산)에서는
    # 이 채보가 들어가고 16.91 동점 둘 중 하나(幻想ロードオブキング, 제목순 뒤)가 밀려나 있다.
    await conn.execute(_PLANT_SQL, (player_id, "심어둔곡", "16.94"))
    await conn.commit()
    fetch = _fixture_fetch()
    # 낮은 여섯 채보가 전부 올라 이번 베스트30 최저가 16.95(初音 14.8+2.15)가 된다 —
    # 심어 둔 16.94는 그보다 엄격히 낮으니 진짜 이탈이다.
    for title in _LOW_BEST30:
        fetch.best30 = _replace_in(fetch.best30, title, 1_009_000)
    fetch.best30 = _replace_in(fetch.best30, "I Wanna", 1_008_500)
    fetch.recent_scores = [
        _recent("幻想ロードオブキング", "MAS", 1_009_000, datetime(2026, 10, 12, 20, 0, tzinfo=_JST)),
        _recent("I Wanna", "MAS", 1_008_500, datetime(2026, 10, 12, 20, 5, tzinfo=_JST)),
    ]

    result = await service.save_sync(player_id, fetch, ctx)

    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert diff.frame_exits == [FrameExit(song_title="심어둔곡", difficulty="MAS", old_rating=Decimal("16.94"))]
    by_title = {u.song_title: u for u in diff.chart_updates}
    assert by_title["幻想ロードオブキング"].entered_frame is True
    assert by_title["I Wanna"].entered_frame is False


async def test_tied_rating_outside_real_frame_is_not_reported_as_exit(conn, monkeypatch) -> None:
    """회귀 테스트: 베스트30 최저(16.91)와 같은 레이팅인데 실제로는 프레임 밖인 채보가
    records_pb 재정렬에서는 제목순으로 30위 안에 들어간다. 이 동점 차이 때문에 새 플레이가
    없는 동기화마다 "이탈"로 뜨면 안 된다."""
    _, player_id, ctx, service = await _setup(conn, songdb=_songdb_with_fixture_new20(monkeypatch))
    await _baseline(service, player_id, ctx, monkeypatch)
    # "A"로 시작하는 제목이라 동점 16.91 안에서 Tempestissimo·幻想ロードオブキング보다 앞에 정렬된다.
    await conn.execute(_PLANT_SQL, (player_id, "A동점곡", "16.91"))
    await conn.commit()

    for _ in range(3):
        result = await service.save_sync(player_id, _fixture_fetch(), ctx)
        diff = await service.sync_diff(result.sync_id)
        assert diff is not None
        assert result.has_new_plays is False
        assert diff.frame_exits == []


async def test_previous_ratings_come_from_previous_log_and_pb_frames(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb=_songdb_with_fixture_new20(monkeypatch))
    await _baseline(service, player_id, ctx, monkeypatch)
    fetch = _fixture_fetch()
    fetch.best30 = _replace_in(fetch.best30, "幻想ロードオブキング", 1_009_500)
    fetch.recent_scores = [
        _recent("幻想ロードオブキング", "MAS", 1_009_500, datetime(2026, 10, 12, 20, 0, tzinfo=_JST)),
    ]

    result = await service.save_sync(player_id, fetch, ctx)

    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert diff.previous_computed_rating == Decimal("16.9480")
    assert diff.previous_best30_average == Decimal("17.0216")
    assert diff.previous_new20_average == Decimal("16.8375")
    # 지금값은 이번 저장본(갱신된 베스트30) 기준이다 — 이전값과 따로 계산된다.
    songdb = SongDbService()
    expected = average_rating(
        [chart_rating(pb.score, songdb.const_for(pb.song.title, pb.chart.difficulty.short())) for pb in fetch.best30]
    )
    assert result.best30_average == expected
    assert result.new20_average == Decimal("16.8375")


async def test_all_personal_bests_outside_frames_are_saved(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn)
    fetch = _fixture_fetch()
    fetch.all_personal_bests = {
        "MAS": [_personal_best("B.B.K.K.B.K.K.", "MAS", 1_000_000), fetch.best30[0]],
        "EXP": [],
    }
    _freeze_now(monkeypatch, utc=_T1_UTC, local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))

    await service.save_sync(player_id, fetch, ctx)

    rows = await service.all_pb_rows(player_id)
    assert len(rows) == 51  # 베스트30 + 신곡20 + 프레임 밖 1(겹치는 best30[0]은 한 번만)
    pb = await service.pb_row(player_id, song_title="B.B.K.K.B.K.K.", difficulty="MAS")
    assert pb is not None
    assert pb == PbRow(
        song_title="B.B.K.K.B.K.K.", difficulty="MAS", score=1_000_000, rank="SS",
        clear_lamp=pb.clear_lamp, combo_lamp=pb.combo_lamp, chain_lamp=pb.chain_lamp,
        const=12.5, rating=chart_rating(1_000_000, 12.5), session_id=None,
        updated_at=_T1_UTC.isoformat(),
    )


async def test_newly_seen_chart_outside_recent_history_is_estimated_update(conn, monkeypatch) -> None:
    """처음 보는 채보(old_score=None)도 이미 있던 채보의 갱신과 똑같이 다룬다:
    최근 이력에서 못 찾으면 이번 동기화의 가장 늦은 세션에 estimated로 붙어 갱신 목록에 들어간다."""
    _, player_id, ctx, service = await _setup(conn)
    await _baseline(service, player_id, ctx, monkeypatch)
    odin = next(pb for pb in _fixture_fetch().best30 if pb.song.title == "Odin")
    fetch = _fixture_fetch()
    fetch.all_personal_bests = {"MAS": [_personal_best("B.B.K.K.B.K.K.", "MAS", 1_000_000)]}
    fetch.recent_scores = [_recent("Odin", "MAS", odin.score - 1000, datetime(2026, 10, 12, 20, 0, tzinfo=_JST))]

    result = await service.save_sync(player_id, fetch, ctx)

    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert [s.play_date for s in diff.sessions] == ["2026-10-12"]
    assert len(diff.chart_updates) == 1
    update = diff.chart_updates[0]
    assert (update.song_title, update.difficulty) == ("B.B.K.K.B.K.K.", "MAS")
    assert (update.old_score, update.old_rank, update.old_rating) == (None, None, None)
    assert update.new_score == 1_000_000
    assert update.session_id == diff.sessions[0].id
    assert update.estimated_session is True
    assert await service.best_score(player_id, song_title="B.B.K.K.B.K.K.", difficulty="MAS") == 1_000_000


async def test_newly_seen_chart_in_recent_history_is_reported_as_update(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn)
    await _baseline(service, player_id, ctx, monkeypatch)
    fetch = _fixture_fetch()
    fetch.all_personal_bests = {"MAS": [_personal_best("B.B.K.K.B.K.K.", "MAS", 1_000_000)]}
    fetch.recent_scores = [_recent("B.B.K.K.B.K.K.", "MAS", 1_000_000, datetime(2026, 10, 12, 20, 0, tzinfo=_JST))]

    result = await service.save_sync(player_id, fetch, ctx)

    diff = await service.sync_diff(result.sync_id)
    assert diff is not None
    assert len(diff.chart_updates) == 1
    update = diff.chart_updates[0]
    assert (update.old_score, update.old_rank, update.old_rating) == (None, None, None)
    assert update.new_score == 1_000_000
    assert update.estimated_session is False


async def test_pb_row_unknown_rating_is_none(conn, monkeypatch) -> None:
    songdb = SongDbService()
    _, player_id, ctx, service = await _setup(conn, songdb=songdb)
    fetch = _fixture_fetch()
    unknown_title = fetch.new20[0].song.title
    real_const_for = songdb.const_for
    monkeypatch.setattr(
        songdb, "const_for",
        lambda title, difficulty: None if title == unknown_title else real_const_for(title, difficulty),
    )
    await service.save_sync(player_id, fetch, ctx)

    pb = await service.pb_row(player_id, song_title=unknown_title, difficulty="MAS")

    assert pb is not None and pb.rating is None and pb.const is None
    assert await service.pb_row(player_id, song_title="존재하지않는곡", difficulty="MAS") is None


async def test_latest_overpower(conn) -> None:
    _, player_id, ctx, service = await _setup(conn)
    assert await service.latest_overpower(player_id) is None

    result = await service.save_sync(player_id, _fixture_fetch(), ctx)
    assert (result.overpower_value, result.overpower_percentage) == (46854.55, 35.86)
    assert await service.latest_overpower(player_id) == (46854.55, 35.86)

    fetch = _fixture_fetch()
    fetch.profile = dataclasses.replace(fetch.profile, over_power=None)
    result = await service.save_sync(player_id, fetch, ctx)
    assert result.overpower_value is None and result.overpower_percentage is None
    assert await service.latest_overpower(player_id) == (None, None)


async def test_latest_sync_summary(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb=_songdb_with_fixture_new20(monkeypatch))
    assert await service.latest_sync_summary(player_id) is None
    _freeze_now(monkeypatch, utc=_T1_UTC, local=datetime(2026, 10, 10, 21, 0, tzinfo=UTC))
    fetch = _fixture_fetch()
    await service.save_sync(player_id, fetch, ctx)

    summary = await service.latest_sync_summary(player_id)

    assert summary is not None
    assert summary.nickname == fetch.profile.username
    assert summary.computed_rating == Decimal("16.9480")
    assert summary.best30_average == Decimal("17.0216")
    assert summary.new20_average == Decimal("16.8375")
    assert summary.last_synced_at == _T1_UTC.isoformat()


async def test_candidates_stay_in_rank_3_to_8_band_exclude_frame_and_sort_by_upside(conn) -> None:
    songdb = SongDbService()
    _, player_id, ctx, service = await _setup(conn, songdb=songdb)
    await service.save_sync(player_id, _fixture_fetch(), ctx)
    rows = await service.all_pb_rows(player_id)
    # Chunimaru 덧씌우기 없는 songdb라 신곡이 없다 → 베스트 프레임 = records_pb 레이팅 상위 30.
    known = sorted((r for r in rows if r.rating is not None), key=lambda r: r.rating or Decimal(0), reverse=True)
    frame_keys = {(r.song_title, r.difficulty) for r in known[:30]}
    scores = {(r.song_title, r.difficulty): r.score for r in rows}
    floor = known[29].rating
    band = [r.rating for r in known[2:8]]  # 프레임 3~8위 레이팅
    assert floor is not None and all(r is not None for r in band)
    band_low, band_high = min(band), max(band)  # pyright: ignore[reportArgumentType]

    hits = await service.candidates(player_id, min_const=14.0, max_const=17.0, count=500)

    assert 0 < len(hits)
    hit_keys = {(h.song_title, h.difficulty) for h in hits}
    assert not hit_keys & frame_keys
    assert ("Wizdomiot", "MAS") not in hit_keys  # 상수 15.0, 프레임 안
    upsides = [h.upside for h in hits]
    assert upsides == sorted(upsides, reverse=True)
    for hit in hits:
        assert band_low <= Decimal(str(hit.const)) + Decimal("2.00") <= band_high
        assert hit.upside == candidate_upside(hit.const, floor)
        assert hit.upside > 0
        assert hit.current_score == scores.get((hit.song_title, hit.difficulty), 0)
        expected = remaining_to_target(
            songdb.note_count_for(hit.song_title, hit.difficulty), hit.current_score, 1_007_500
        )
        assert (hit.remaining_score, hit.remaining_attacks) == (expected if expected else (None, None))


async def test_diff_cache_keeps_only_newest(conn, monkeypatch) -> None:
    monkeypatch.setattr(records_module, "_DIFF_CACHE_LIMIT", 2)
    _, player_id, ctx, service = await _setup(conn)
    await service.save_sync(player_id, _fixture_fetch(), ctx)
    sync_ids = [(await service.save_sync(player_id, _fixture_fetch(), ctx)).sync_id for _ in range(3)]

    assert await service.sync_diff(sync_ids[0]) is None
    assert await service.sync_diff(sync_ids[1]) is not None
    assert await service.sync_diff(sync_ids[2]) is not None
    assert await service.sync_diff("없는-id") is None


async def test_current_frames_returns_best30_and_new20_in_rating_order(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb=_songdb_with_fixture_new20(monkeypatch))
    fetch = _fixture_fetch()
    # 프레임 밖 채보가 하나 더 있어도 30/20개로 잘려야 한다.
    fetch.all_personal_bests = {"MAS": [_personal_best("B.B.K.K.B.K.K.", "MAS", 900_000)]}
    await service.save_sync(player_id, fetch, ctx)

    best, new = await service.current_frames(player_id)

    assert len(best) == 30
    assert len(new) == 20
    assert {r.song_title for r in new} == {pb.song.title for pb in fetch.new20}
    for frame in (best, new):
        ratings = [r.rating or Decimal(0) for r in frame]
        assert all(r.rating is not None for r in frame)
        assert ratings == sorted(ratings, reverse=True)
    assert ("B.B.K.K.B.K.K.", "MAS") not in {(r.song_title, r.difficulty) for r in (*best, *new)}
    summary = await service.latest_sync_summary(player_id)
    assert summary is not None
    assert average_rating([r.rating for r in best]) == summary.best30_average


async def test_current_frames_empty_when_never_synced(conn) -> None:
    _, player_id, _, service = await _setup(conn)

    assert await service.current_frames(player_id) == ([], [])


async def test_rating_deltas_none_before_any_sync(conn) -> None:
    _, player_id, _, service = await _setup(conn)

    assert await service.rating_deltas(player_id) is None


async def test_rating_deltas_first_sync_has_no_previous(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb=_songdb_with_fixture_new20(monkeypatch))
    await _baseline(service, player_id, ctx, monkeypatch)

    deltas = await service.rating_deltas(player_id)

    assert deltas is not None
    assert deltas.has_previous is False
    assert (deltas.computed_rating, deltas.best30_average, deltas.new20_average) == (
        Decimal("16.9480"), Decimal("17.0216"), Decimal("16.8375"),
    )
    assert (deltas.previous_computed_rating, deltas.previous_best30_average, deltas.previous_new20_average) == (
        None, None, None,
    )


async def test_rating_deltas_second_sync_compares_with_previous_snapshot(conn, monkeypatch) -> None:
    _, player_id, ctx, service = await _setup(conn, songdb=_songdb_with_fixture_new20(monkeypatch))
    await _baseline(service, player_id, ctx, monkeypatch)
    fetch = _fixture_fetch()
    lowest = min(fetch.best30, key=lambda pb: pb.score)
    fetch.best30 = _replace_in(fetch.best30, lowest.song.title, 1_010_000)

    result = await service.save_sync(player_id, fetch, ctx)
    deltas = await service.rating_deltas(player_id)

    assert deltas is not None and deltas.has_previous is True
    assert (deltas.previous_computed_rating, deltas.previous_best30_average, deltas.previous_new20_average) == (
        Decimal("16.9480"), Decimal("17.0216"), Decimal("16.8375"),
    )
    assert deltas.computed_rating == result.computed_rating
    assert deltas.best30_average == result.best30_average
    assert deltas.new20_average == Decimal("16.8375")
    assert deltas.best30_average is not None and deltas.best30_average > Decimal("17.0216")


async def test_snapshot_averages_store_unknown_as_not_computable(conn, monkeypatch) -> None:
    songdb = _songdb_with_fixture_new20(monkeypatch)
    _, player_id, ctx, service = await _setup(conn, songdb=songdb)
    fetch = _fixture_fetch()
    unknown_title = fetch.new20[0].song.title
    real_const_for = songdb.const_for
    monkeypatch.setattr(
        songdb, "const_for",
        lambda title, difficulty: None if title == unknown_title else real_const_for(title, difficulty),
    )
    await service.save_sync(player_id, fetch, ctx)

    cursor = await conn.execute(
        "SELECT best30_average_snapshot, new20_average_snapshot FROM records_sync_log WHERE player_id = ?",
        (player_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert (row[0], row[1]) == ("17.0216", "계산 불가")
    deltas = await service.rating_deltas(player_id)
    assert deltas is not None and deltas.new20_average is None
