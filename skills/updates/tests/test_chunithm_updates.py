# skills/updates/tests/test_chunithm_updates.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.scheduler import Scheduler
from core.tables import RowRef, render_text
from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.common.settings import SettingsService
from services.common.yuni import YuniService

_SKILL_DIR = Path(__file__).parent.parent
_SKILL_PY = _SKILL_DIR / "skill.py"
_REFERENCE_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "reference" / "chunithm-updates"

_CORE_PLAYERS_SQL = Path("migrations/core/001_players.sql").read_text()
_CORE_PLAYER_TAGS_SQL = Path("migrations/core/002_player_tags.sql").read_text()
_CORE_SETTINGS_SQL = Path("migrations/core/003_settings.sql").read_text()
_CORE_SCHEDULES_SQL = Path("migrations/core/006_schedules.sql").read_text()
_UPDATES_SQL = (_SKILL_DIR / "migrations" / "001_init.sql").read_text()

_INTL_NEWS_URL = "https://info-chunithm.sega.com/"
_JP_FEED_URL = "https://info-chunithm.sega.jp/feed/"
_INTL_MUSIC_URL = "https://chunithm.sega.com/assets/data/music.json"
_JP_MUSIC_URL = "https://chunithm.sega.jp/storage/json/music.json"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_updates_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript(_CORE_PLAYERS_SQL)
    await conn.executescript(_CORE_PLAYER_TAGS_SQL)
    await conn.executescript(_CORE_SETTINGS_SQL)
    await conn.executescript(_CORE_SCHEDULES_SQL)
    await conn.executescript(_UPDATES_SQL)
    try:
        yield conn
    finally:
        await conn.close()


@dataclass(slots=True, kw_only=True)
class _FakeResponse:
    body: str
    fail: bool = False

    @property
    def text(self) -> str:
        return self.body

    def raise_for_status(self) -> None:
        if self.fail:
            raise RuntimeError("http 오류")


@dataclass(slots=True, kw_only=True)
class _FakeHttp:
    pages: dict[str, str] = field(default_factory=dict)
    json_data: dict[str, Any] = field(default_factory=dict)

    async def get(self, url: str) -> _FakeResponse:
        return _FakeResponse(body=self.pages[url])

    async def get_json(self, url: str) -> Any:
        return self.json_data[url]


@dataclass(slots=True, kw_only=True)
class _FakeSongDb:
    refresh_calls: int = 0
    hits: list[Any] = field(default_factory=list)
    known_titles: set[str] = field(default_factory=set)

    def song_by_title(self, title: str) -> Any | None:
        return object() if title in self.known_titles else None

    async def refresh_from_chunimaru(self, http: Any) -> None:
        self.refresh_calls += 1

    def find_by_const(self, *, min_const: float, max_const: float, difficulty: str | None = None) -> list[Any]:
        return self.hits


@dataclass(slots=True, kw_only=True)
class _FakeRecords:
    floor: Decimal = Decimal("10.00")

    async def new_frame_floor(self, player_id: str) -> Decimal:
        return self.floor


@dataclass(slots=True, kw_only=True)
class _FakeNotify:
    posts: list[tuple[str, str]] = field(default_factory=list)
    dms: list[tuple[str, str]] = field(default_factory=list)

    async def post(self, channel_id: str, content: str) -> None:
        self.posts.append((channel_id, content))

    async def dm(self, player_id: str, content: str, *, view: Any | None = None) -> None:
        self.dms.append((player_id, content))


def _ctx(
    *,
    db: aiosqlite.Connection,
    http: _FakeHttp | None = None,
    songdb: _FakeSongDb | None = None,
    records: _FakeRecords | None = None,
    notify: _FakeNotify | None = None,
    player_id: str = "system",
    guild_id: str | None = None,
) -> ToolContext:
    services: dict[str, object] = {
        "http": http or _FakeHttp(),
        "songdb": songdb or _FakeSongDb(),
        "records": records or _FakeRecords(),
        "notify": notify or _FakeNotify(),
        "settings": SettingsService(db),
        "players": PlayersService(db),
        "yuni": YuniService(),
    }
    return ToolContext(
        player_id=player_id, tags=frozenset(), services=services, db=db,
        scheduler=Scheduler(db, quiet_start=time(2, 0), quiet_end=time(6, 0)),
        event_bus=EventBus(), guild_id=guild_id,
    )


async def _enable_channel(ctx: ToolContext, *, guild_id: str, channel_id: str = "chan-1") -> None:
    settings: SettingsService = ctx.settings  # type: ignore[assignment]
    await settings.set_value(
        scope="guild", scope_id=guild_id, skill="updates", name="알림 채널",
        value=channel_id, updated_by="tester",
    )


# ---------------------------------------------------------------- _check_notices


async def test_check_notices_schedules_dated_updates_and_broadcasts_dateless_one_immediately(
    skill: ModuleType, db: aiosqlite.Connection,
) -> None:
    http = _FakeHttp(pages={_INTL_NEWS_URL: (_REFERENCE_DIR / "intl_news_sample.html").read_text(encoding="utf-8")})
    notify = _FakeNotify()
    ctx = _ctx(db=db, http=http, notify=notify)
    await _enable_channel(ctx, guild_id="g1")

    found = await skill._check_notices(ctx, skill.Region.INTL)
    assert found is True

    cursor = await db.execute("SELECT url, update_date, notified, schedule_id FROM updates_notices ORDER BY id")
    rows = list(await cursor.fetchall())
    assert len(rows) == 6
    dated = [r for r in rows if r["update_date"] is not None]
    dateless = [r for r in rows if r["update_date"] is None]
    assert len(dated) == 5
    assert len(dateless) == 1
    # 날짜가 있는 공지는 예약만 걸고 아직 안 알린다.
    assert all(r["notified"] == 0 and r["schedule_id"] is not None for r in dated)
    # 날짜를 못 뽑은 공지(버전 알림 글)는 바로 알린다.
    assert dateless[0]["notified"] == 1
    assert len(notify.posts) == 1
    assert "chan-1" == notify.posts[0][0]


async def test_check_notices_flags_version_change_only_for_the_chronologically_latest_item(
    skill: ModuleType, db: aiosqlite.Connection,
) -> None:
    """공지 페이지는 최신 글이 먼저 온다. 한 번에 여러 공지를 처음 볼 때(켜진 직후 등)
    옛 버전 글을 나중에 읽어 거꾸로 "버전이 바뀌었다"고 잘못 보면 안 된다 — 실제
    저장본은 맨 위(최신)만 새 버전("CHUNITHM Mate")이고 나머지는 전부 이전 버전이다."""
    http = _FakeHttp(pages={_INTL_NEWS_URL: (_REFERENCE_DIR / "intl_news_sample.html").read_text(encoding="utf-8")})
    notify = _FakeNotify()
    players = PlayersService(db)
    admin = await players.create_player()
    await players.grant_tag(admin.id, "admin", source="test")
    ctx = _ctx(db=db, http=http, notify=notify)
    await _enable_channel(ctx, guild_id="g1")

    await skill._check_notices(ctx, skill.Region.INTL)

    assert len(notify.dms) == 1
    assert "CHUNITHM Mate" in notify.dms[0][1]


async def test_check_notices_skips_urls_already_known(skill: ModuleType, db: aiosqlite.Connection) -> None:
    html = (_REFERENCE_DIR / "intl_news_sample.html").read_text(encoding="utf-8")
    http = _FakeHttp(pages={_INTL_NEWS_URL: html})
    ctx = _ctx(db=db, http=http)
    await _enable_channel(ctx, guild_id="g1")

    assert await skill._check_notices(ctx, skill.Region.INTL) is True
    assert await skill._check_notices(ctx, skill.Region.INTL) is False

    cursor = await db.execute("SELECT COUNT(*) FROM updates_notices")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 6


# ---------------------------------------------------------------- on_notice_due


async def test_on_notice_due_does_not_notify_twice(skill: ModuleType, db: aiosqlite.Connection) -> None:
    notice_id = await skill.models.insert_notice(
        db, region="intl", url="https://info-chunithm.sega.com/9999/", title="『V』Details of 1/1 Update",
        posted_at="2026-12-31", update_date="2027-01-01", version_name="V", created_at="2026-12-31T00:00:00",
    )
    notify = _FakeNotify()
    ctx = _ctx(db=db, notify=notify)
    await _enable_channel(ctx, guild_id="g1")

    await skill.on_notice_due(ctx, {"notice_id": notice_id, "version_changed": False})
    await skill.on_notice_due(ctx, {"notice_id": notice_id, "version_changed": False})

    assert len(notify.posts) == 1


# ---------------------------------------------------------------- _check_song_list


async def test_check_song_list_first_check_only_sets_baseline(skill: ModuleType, db: aiosqlite.Connection) -> None:
    http = _FakeHttp(json_data={_INTL_MUSIC_URL: [{"id": "1", "title": "곡A"}, {"id": "2", "title": "곡B"}]})
    songdb = _FakeSongDb()
    notify = _FakeNotify()
    ctx = _ctx(db=db, http=http, songdb=songdb, notify=notify)
    await _enable_channel(ctx, guild_id="g1")

    await skill._check_song_list(ctx, skill.Region.INTL, notice_found_this_tick=False)

    assert notify.posts == []
    assert songdb.refresh_calls == 0
    snapshot = await skill.models.latest_song_snapshot(db, region="intl")
    assert snapshot is not None
    assert snapshot.added == []
    assert snapshot.song_ids == {"1": "곡A", "2": "곡B"}


async def test_check_song_list_broadcasts_added_songs_when_no_notice_found(
    skill: ModuleType, db: aiosqlite.Connection,
) -> None:
    http = _FakeHttp(json_data={_INTL_MUSIC_URL: [{"id": "1", "title": "곡A"}]})
    songdb = _FakeSongDb()
    notify = _FakeNotify()
    ctx = _ctx(db=db, http=http, songdb=songdb, notify=notify)
    await _enable_channel(ctx, guild_id="g1")
    await skill._check_song_list(ctx, skill.Region.INTL, notice_found_this_tick=False)  # 기준점

    http.json_data[_INTL_MUSIC_URL] = [{"id": "1", "title": "곡A"}, {"id": "2", "title": "새곡"}]
    await skill._check_song_list(ctx, skill.Region.INTL, notice_found_this_tick=False)

    assert songdb.refresh_calls == 1
    assert len(notify.posts) == 1
    assert "새곡" in notify.posts[0][1]


async def test_check_song_list_suppresses_broadcast_when_a_notice_was_found_this_tick(
    skill: ModuleType, db: aiosqlite.Connection,
) -> None:
    http = _FakeHttp(json_data={_INTL_MUSIC_URL: [{"id": "1", "title": "곡A"}]})
    notify = _FakeNotify()
    ctx = _ctx(db=db, http=http, notify=notify)
    await _enable_channel(ctx, guild_id="g1")
    await skill._check_song_list(ctx, skill.Region.INTL, notice_found_this_tick=False)  # 기준점

    http.json_data[_INTL_MUSIC_URL] = [{"id": "1", "title": "곡A"}, {"id": "2", "title": "새곡"}]
    await skill._check_song_list(ctx, skill.Region.INTL, notice_found_this_tick=True)

    assert notify.posts == []
    snapshot = await skill.models.latest_song_snapshot(db, region="intl")
    assert snapshot is not None
    assert snapshot.added == [("2", "새곡")]


async def test_check_song_list_jp_respects_jp_updates_toggle(skill: ModuleType, db: aiosqlite.Connection) -> None:
    http = _FakeHttp(json_data={_JP_MUSIC_URL: [{"id": "1", "title": "곡A"}]})
    notify = _FakeNotify()
    ctx = _ctx(db=db, http=http, notify=notify)
    await _enable_channel(ctx, guild_id="g1")
    settings: SettingsService = ctx.settings  # type: ignore[assignment]
    await settings.set_value(
        scope="guild", scope_id="g1", skill="updates", name="일본판 알림", value=False, updated_by="tester",
    )
    await skill._check_song_list(ctx, skill.Region.JP, notice_found_this_tick=False)  # 기준점

    http.json_data[_JP_MUSIC_URL] = [{"id": "1", "title": "곡A"}, {"id": "2", "title": "새곡"}]
    await skill._check_song_list(ctx, skill.Region.JP, notice_found_this_tick=False)

    assert notify.posts == []


# ---------------------------------------------------------------- on_sync_completed


async def test_on_sync_completed_returns_none_without_new_plays(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    section = await skill.on_sync_completed(ctx, {"has_new_plays": False, "previous_synced_at": "2026-01-01"})
    assert section is None


async def test_on_sync_completed_filters_by_new_frame_floor(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await skill.models.insert_song_snapshot(
        db, region="intl", checked_at="2026-10-10T12:00:00",
        song_ids={"1": "낮은상수곡", "2": "높은상수곡"},
        added=[("1", "낮은상수곡"), ("2", "높은상수곡")], removed=[],
    )
    songdb = _FakeSongDb(
        hits=[
            SimpleNamespace(song=SimpleNamespace(title="낮은상수곡"), chart=SimpleNamespace(const=5.0)),
            SimpleNamespace(song=SimpleNamespace(title="높은상수곡"), chart=SimpleNamespace(const=16.0)),
        ]
    )
    records = _FakeRecords(floor=Decimal("15.00"))
    ctx = _ctx(db=db, songdb=songdb, records=records)

    section = await skill.on_sync_completed(
        ctx, {"has_new_plays": True, "previous_synced_at": "2026-10-01T00:00:00", "player_id": "p1"},
    )

    assert section is not None
    assert section.data["new_songs_fit"] == ["높은상수곡"]


# ---------------------------------------------------------------- 도구: next / new_songs


async def test_next_tool_reports_no_notice_when_none_upcoming(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    result = await skill.next(ctx, "intl")
    assert result.data["update_date"] is None


async def test_next_tool_returns_the_soonest_upcoming_update(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await skill.models.insert_notice(
        db, region="intl", url="https://info-chunithm.sega.com/1/", title="지난 업데이트",
        posted_at="2020-01-01", update_date="2020-01-02", version_name=None, created_at="2020-01-01T00:00:00",
    )
    await skill.models.insert_notice(
        db, region="intl", url="https://info-chunithm.sega.com/2/", title="다음 업데이트",
        posted_at="2099-01-01", update_date="2099-01-02", version_name=None, created_at="2099-01-01T00:00:00",
    )
    ctx = _ctx(db=db)
    result = await skill.next(ctx, "intl")
    assert result.data["update_date"] == "2099-01-02"


async def test_new_songs_tool_reports_added_and_removed(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await skill.models.insert_song_snapshot(
        db, region="jp", checked_at="2026-10-10T12:00:00",
        song_ids={"1": "곡A"}, added=[("1", "곡A")], removed=[("9", "빠진곡")],
    )
    ctx = _ctx(db=db)
    result = await skill.new_songs(ctx, "jp")
    assert result.data["added"] == ["곡A"]
    assert result.data["removed"] == ["빠진곡"]
    table = result.table
    assert table is not None and table.unit == "곡"
    assert [g.title for g in table.groups] == ["추가", "삭제"]
    assert dict(table.summary) == {"지역": "일본판", "확인": "2026-10-10"}
    added, removed = table.rows
    assert added.ref == RowRef("updates.listed_song", '["jp", "곡A"]')
    assert not added.cell("곡").filled and added.cell("곡 이름(원문)").text == "곡A"  # 일본판은 곡 칸을 비운다
    assert removed.cell("구분").text == "삭제"
    text = render_text(table).content
    assert "── 추가 · 1곡 ──" in text and "── 삭제 · 1곡 ──" in text and "곡 이름(원문) 곡A" in text


async def test_new_songs_table_fills_song_cell_only_for_known_intl_additions(
    skill: ModuleType, db: aiosqlite.Connection
) -> None:
    await skill.models.insert_song_snapshot(
        db, region="intl", checked_at="2026-10-10T12:00:00",
        song_ids={"1": "곡A", "2": "곡B"}, added=[("1", "곡A"), ("2", "곡B")], removed=[("9", "곡A")],
    )
    ctx = _ctx(db=db, songdb=_FakeSongDb(known_titles={"곡A"}))

    result = await skill.new_songs(ctx, "intl")

    assert result.table is not None
    known, unknown, removed = result.table.rows
    assert known.cell("곡").raw == "곡A" and not known.cell("곡 이름(원문)").filled  # 곡 단위 행
    assert not unknown.cell("곡").filled and unknown.cell("곡 이름(원문)").text == "곡B"
    assert not removed.cell("곡").filled  # 삭제된 곡은 곡 DB에 있어도 곡 칸을 비운다


async def test_new_songs_without_changes_is_a_message(skill: ModuleType, db: aiosqlite.Connection) -> None:
    await skill.models.insert_song_snapshot(
        db, region="intl", checked_at="2026-10-10T12:00:00", song_ids={}, added=[], removed=[],
    )
    result = await skill.new_songs(_ctx(db=db), "intl")

    assert result.table is None and result.display is not None
    assert "추가: 없음" in (result.display.content or "") and "삭제: 없음" in (result.display.content or "")


async def test_new_songs_tool_rejects_unknown_region(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db)
    result = await skill.new_songs(ctx, "kr")
    assert result.error is not None


# ---------------------------------------------------------------- 도구: configure


async def test_configure_updates_only_given_values(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, guild_id="g1")
    result = await skill.configure(ctx, channel="<#111111111111111111>")
    assert result.data == {"channel_id": "111111111111111111"}

    result2 = await skill.configure(ctx, role_mention="<@&222222222222222222>")
    assert result2.data == {"role_mention": "<@&222222222222222222>"}

    settings: SettingsService = ctx.settings  # type: ignore[assignment]
    channel = await settings.get_value(
        scope="guild", scope_id="g1", skill="updates", name="알림 채널", default=None,
    )
    assert channel == "111111111111111111"


async def test_configure_rejects_unparseable_channel(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, guild_id="g1")
    result = await skill.configure(ctx, channel="아무거나")
    assert result.error is not None


async def test_configure_requires_at_least_one_value(skill: ModuleType, db: aiosqlite.Connection) -> None:
    ctx = _ctx(db=db, guild_id="g1")
    result = await skill.configure(ctx)
    assert result.error is not None
