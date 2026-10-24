from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.songdb import SongDbService
from services.game.songdb import SongRecord, chart_key
from services.maimai.profile import MAIMAI, VERSION_NAMES
from services.maimai.songdb import JACKET_BASE, OTOGE_URL, MaimaiSongDbService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})

# otoge-db music-ex-intl.json의 실제 항목 모양(2026-10-23)을 본뜬 만든 표본이다. 저장소에는 원본을 넣지 않는다.


def _chart(prefix: str, part: str, level: str, const: str, notes: str, designer: str = "") -> dict[str, str]:
    entry = {f"{prefix}{part}": level, f"{prefix}{part}_i": const, f"{prefix}{part}_notes": notes}
    if designer:
        entry[f"{prefix}{part}_designer"] = designer
    return entry


def _entry(title: str, version: str, *, intl: str = "1", **extra: str) -> dict[str, str]:
    return {
        "sort": "100", "title": title, "artist": "아티스트", "catcode": "maimai", "version": version,
        "bpm": "150", "image_url": "abc123.png", "intl": intl, **extra,
    }


def _sample() -> list[dict[str, str]]:
    std_only = _entry(
        "STD Only", "10000",
        **_chart("lev_", "bas", "5", "5.0", "96"), **_chart("lev_", "mas", "12", "12.4", "302", "ニャイン"),
    )
    both = _entry(
        "Both Charts", "20000", sort="200",
        **_chart("lev_", "mas", "12+", "12.6", "346"),
        **_chart("dx_lev_", "mas", "13+", "13.7", "631", "Luxizhel"),
        **_chart("dx_lev_", "remas", "14", "14.2", "700"),
    )
    new_song = _entry("Newest", "26511", sort="300", **_chart("dx_lev_", "mas", "14+", "14.9", "900"))
    last_version = _entry("Last Version", "26500", sort="301", **_chart("dx_lev_", "mas", "13", "13.0", "800"))
    no_const = _entry("No Const", "19000", **_chart("lev_", "exp", "8", "", "250"))
    not_intl = _entry("Japan Only", "26508", intl="0", **_chart("dx_lev_", "mas", "15", "15.0", "1000"))
    utage = _entry("[宴]Utage Song", "24010", lev_utage="13+?", lev_utage_notes="950", lev_utage_designer="譜面作者")
    link_a = _entry("Link", "11004", sort="400", artist="Clean Tears", **_chart("lev_", "mas", "12", "12.5", "500"))
    link_b = _entry("Link", "14000", sort="401", artist="Circle of friends", **_chart("lev_", "mas", "12", "12.5", "510"))
    blank_title = _entry("\u3000", "22504", sort="500", **_chart("dx_lev_", "mas", "9", "9.0", "300"))
    return [blank_title, std_only, both, new_song, last_version, no_const, not_intl, utage, link_a, link_b]


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    path = tmp_path / "maimai" / "music-ex-intl.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_sample(), ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def db(cache: Path) -> MaimaiSongDbService:
    return MaimaiSongDbService(cache)


def _song(db: MaimaiSongDbService, title: str) -> SongRecord:
    song = db.song_by_title(title)
    assert song is not None
    return song


class _Http:
    def __init__(self, data: Any = None, error: Exception | None = None) -> None:
        self.data, self.error, self.urls = data, error, []

    async def get_json(self, url: str) -> Any:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.data


def test_profile_version_order_matches_version_names() -> None:
    assert MAIMAI.version_order == tuple(VERSION_NAMES.values())
    assert MAIMAI.range_codes == ("BAS", "ADV", "EXP", "MAS", "REM")
    assert "UTA" in MAIMAI.chart_codes and "UTA" not in MAIMAI.range_codes


def test_std_and_dx_charts_are_separate_with_const_level_notes_designer(db: MaimaiSongDbService) -> None:
    std = db.chart_for("Both Charts", "MAS", "STD")
    dx = db.chart_for("Both Charts", "MAS", "DX")
    assert std is not None and dx is not None
    assert (std.const, std.level, std.note_count, std.chart_type) == (12.6, "12+", 346, "STD")
    assert (dx.const, dx.level, dx.note_count, dx.designer) == (13.7, "13+", 631, "Luxizhel")
    assert db.const_for("Both Charts", "MAS", "DX") == 13.7
    assert db.note_count_for("Both Charts", "MAS", "STD") == 346
    assert db.const_for("Both Charts", "MAS") is None  # 종류를 안 주면 츄니즘식 키라 찾지 못한다
    assert set(_song(db, "Both Charts").charts) == {
        chart_key("STD", "MAS"), chart_key("DX", "MAS"), chart_key("DX", "REM"),
    }


def test_remaster_chart_is_rem_and_only_on_songs_that_have_it(db: MaimaiSongDbService) -> None:
    assert db.const_for("Both Charts", "REM", "DX") == 14.2
    assert db.chart_for("STD Only", "REM", "STD") is None


def test_missing_const_is_unknown_but_level_and_notes_are_kept(db: MaimaiSongDbService) -> None:
    chart = db.chart_for("No Const", "EXP", "STD")
    assert chart is not None
    assert (chart.const, chart.is_const_unknown, chart.level, chart.note_count) == (None, True, "8", 250)


def test_utage_title_keeps_its_prefix_with_a_constless_uta_chart(db: MaimaiSongDbService) -> None:
    chart = db.chart_for("[宴]Utage Song", "UTA", "STD")
    assert chart is not None
    assert (chart.const, chart.level, chart.note_count, chart.designer) == (None, "13+", 950, "譜面作者")


def test_utage_is_excluded_from_difficulty_ranges_but_found_by_code(db: MaimaiSongDbService) -> None:
    in_range = db.find_songs(min_difficulty="BAS", max_difficulty="REM")
    assert all(hit.chart.difficulty != "UTA" for hit in in_range)
    assert [hit.song.title for hit in db.find_songs(difficulty="UTA")] == ["[宴]Utage Song"]


def test_same_title_entries_merge_with_first_entry_winning(db: MaimaiSongDbService) -> None:
    song = db.song_by_title("Link")
    assert song is not None
    assert song.artist == "Clean Tears"
    assert song.version == "maimai PLUS"
    assert song.charts[chart_key("STD", "MAS")].note_count == 500


def test_version_code_prefix_maps_to_version_name(db: MaimaiSongDbService) -> None:
    assert _song(db, "STD Only").version == "maimai"
    assert _song(db, "Both Charts").version == "maimai DX"
    assert _song(db, "Last Version").version == "CiRCLE PLUS"
    assert _song(db, "Link").version == "maimai PLUS"


def test_unknown_version_prefix_is_kept_as_the_code_and_still_current(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps([*_sample(), _entry("Future", "27000", sort="999", **_chart("dx_lev_", "mas", "15", "15.0", "1"))]))
    db = MaimaiSongDbService(path)
    assert _song(db, "Future").version == "270"
    assert db.current_version() == "270"
    assert db.is_new_for("Future") and not db.is_new_for("Newest")


def test_new_song_is_the_latest_intl_version_not_japan_only_versions(db: MaimaiSongDbService) -> None:
    # 일본판 전용 곡(26508, intl=0)은 현 버전을 올리지 않는다. 국제판 최대는 26511 → 265.
    assert db.current_version() == "CiRCLE PLUS"
    assert db.is_new_for("Newest") and db.is_new_for("Last Version")
    assert not db.is_new_for("Both Charts") and not db.is_new_for("Unknown Title")
    assert [hit.song.title for hit in db.find_songs(new_only=True)] == ["Newest", "Last Version"]


def test_current_version_override_moves_the_new_song_set(db: MaimaiSongDbService) -> None:
    db.set_current_version("maimai DX")
    assert db.current_version() == "maimai DX"
    assert db.is_new_for("Both Charts") and not db.is_new_for("Newest")
    db.set_current_version(None)
    assert db.is_new_for("Newest")
    with pytest.raises(ValueError, match="모르는 maimai 버전"):
        db.set_current_version("없는 버전")


def test_override_given_at_construction_survives_loading_the_cache(cache: Path) -> None:
    db = MaimaiSongDbService(cache, current_version="maimai DX")
    assert db.is_new_for("Both Charts") and not db.is_new_for("Newest")


def test_japan_only_songs_are_not_available(db: MaimaiSongDbService) -> None:
    assert db.song_by_title("Japan Only") is not None
    assert "Japan Only" not in [hit.song.title for hit in db.find_songs()]


def test_find_songs_filters_by_chart_type_const_and_difficulty(db: MaimaiSongDbService) -> None:
    dx = db.find_songs(chart_type="DX", min_const=13.5)
    assert [(h.song.title, h.chart.difficulty, h.chart.const) for h in dx] == [
        ("Newest", "MAS", 14.9), ("Both Charts", "REM", 14.2), ("Both Charts", "MAS", 13.7),
    ]
    std_mas = db.find_songs(chart_type="STD", difficulty="MAS")
    assert {h.song.title for h in std_mas} == {"STD Only", "Both Charts", "Link"}
    both_types = db.find_songs(difficulty="MAS", min_const=12.6, max_const=12.6)
    assert [(h.song.title, h.chart.chart_type) for h in both_types] == [("Both Charts", "STD")]
    assert [h.chart.chart_type for h in db.find_by_const(min_const=14.0, chart_type="DX")] == ["DX", "DX"]
    assert [h.song.title for h in db.find_by_level(level="13+", chart_type="DX")] == ["Both Charts"]
    assert {h.song.title for h in db.find_by_version(version="maimai", chart_type="STD")} == {"STD Only"}


def test_find_songs_rejects_an_unknown_chart_type(db: MaimaiSongDbService) -> None:
    with pytest.raises(ValueError, match="채보 종류"):
        db.find_songs(chart_type="FINALE")


def test_chunithm_song_db_has_no_chart_type_and_keeps_difficulty_keys() -> None:
    chunithm = SongDbService()
    with pytest.raises(ValueError, match="채보 종류"):
        chunithm.find_songs(chart_type="DX")
    song = next(iter(chunithm.find_songs(difficulty="MAS"))).song
    assert "MAS" in song.charts


def test_a_title_made_of_one_fullwidth_space_is_kept_as_is(db: MaimaiSongDbService) -> None:
    assert db.const_for("\u3000", "MAS", "DX") == 9.0


def test_jacket_is_the_otoge_db_url(db: MaimaiSongDbService) -> None:
    assert db.jacket_for("STD Only") == f"{JACKET_BASE}/abc123.png"


def test_artist_and_bpm_come_from_the_entry(db: MaimaiSongDbService) -> None:
    song = db.song_by_title("STD Only")
    assert song is not None and (song.artist, song.bpm) == ("아티스트", 150.0)
    assert [s.title for s in db.find_by_artist(name="아티")][:1] == ["Both Charts"]


@pytest.fixture
async def alias_conn() -> AsyncIterator[aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(
        "".join(
            Path(f"migrations/core/{name}.sql").read_text()
            for name in ("005_arcades", "011_songdb_aliases", "017_arcade_aliases_guild_scope", "020_aliases_global",
                         "025_songdb_aliases_game")
        )
    )
    await conn.commit()
    try:
        yield conn
    finally:
        await conn.close()


async def test_resolve_finds_by_title_substring_and_server_alias(
    cache: Path, alias_conn: aiosqlite.Connection
) -> None:
    db = MaimaiSongDbService(cache, conn=alias_conn)
    assert (await db.resolve("Both Charts", _CTX)).title == "Both Charts"
    with pytest.raises(ResolutionError) as partial:  # 부분 일치는 확정하지 않고 후보로 돌려준다(대소문자 무시)
        await db.resolve("newest", _CTX)
    assert partial.value.candidates == ["Newest"]
    with pytest.raises(ResolutionError):
        await db.resolve("Japan Only", _CTX)  # 국제판에 없는 곡은 고를 수 없다
    await db.add_alias(alias="별칭", song_title="Newest", added_by="p")
    assert (await db.resolve("별칭", _CTX)).title == "Newest"


async def test_aliases_are_per_game(cache: Path, alias_conn: aiosqlite.Connection) -> None:
    maimai = MaimaiSongDbService(cache, conn=alias_conn)
    await maimai.add_alias(alias="공통별칭", song_title="Newest", added_by="p")
    assert (await maimai.get_alias(alias="공통별칭")) is not None
    chunithm = SongDbService(conn=alias_conn)
    assert await chunithm.get_alias(alias="공통별칭") is None
    await chunithm.add_alias(alias="공통별칭", song_title="Chronomia", added_by="p")
    assert (await maimai.get_alias(alias="공통별칭") or pytest.fail("없음")).song_title == "Newest"
    await maimai.remove_alias(alias="공통별칭")
    assert await maimai.get_alias(alias="공통별칭") is None
    assert await chunithm.get_alias(alias="공통별칭") is not None


async def test_refresh_replaces_songs_and_writes_the_cache(tmp_path: Path) -> None:
    path = tmp_path / "data" / "maimai" / "music-ex-intl.json"
    db = MaimaiSongDbService(path)
    assert db.find_songs() == [] and db.current_version() is None
    http = _Http(_sample())
    assert await db.refresh_from_otoge(http) is True
    assert http.urls == [OTOGE_URL]
    assert db.current_version() == "CiRCLE PLUS" and db.is_new_for("Newest")
    assert json.loads(path.read_text(encoding="utf-8")) == _sample()
    assert MaimaiSongDbService(path).const_for("Both Charts", "MAS", "DX") == 13.7  # 다음 기동은 캐시로 시작한다


@pytest.mark.parametrize(
    "bad",
    [[], {"songs": []}, [{"title": "x"}], [{"version": "10000"}], "html"],
    ids=["empty", "wrong-shape", "no-version", "no-title", "string"],
)
async def test_refresh_with_bad_data_keeps_memory_and_cache(cache: Path, bad: Any) -> None:
    before = cache.read_text(encoding="utf-8")
    db = MaimaiSongDbService(cache)
    assert await db.refresh_from_otoge(_Http(bad)) is False
    assert cache.read_text(encoding="utf-8") == before
    assert db.const_for("Both Charts", "MAS", "DX") == 13.7


async def test_refresh_network_error_keeps_memory(cache: Path) -> None:
    db = MaimaiSongDbService(cache)
    assert await db.refresh_from_otoge(_Http(error=RuntimeError("down"))) is False
    assert db.current_version() == "CiRCLE PLUS"


def test_broken_cache_starts_empty_instead_of_crashing(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("not json")
    db = MaimaiSongDbService(path)
    assert db.find_songs() == []


async def test_refresh_if_due_waits_a_day_after_success_and_an_hour_after_failure(db: MaimaiSongDbService) -> None:
    broken = _Http(error=RuntimeError("down"))
    assert await db.refresh_if_due(broken, now=1000.0) is False
    assert len(broken.urls) == 1
    assert await db.refresh_if_due(broken, now=1000.0 + 3599) is False
    assert len(broken.urls) == 1  # 한 시간 안에는 다시 두드리지 않는다

    good = _Http(data=_sample())
    assert await db.refresh_if_due(good, now=1000.0 + 3600) is True
    assert await db.refresh_if_due(good, now=1000.0 + 3600 + 86_399) is False
    assert len(good.urls) == 1
    assert await db.refresh_if_due(good, now=1000.0 + 3600 + 86_400) is True
    assert len(good.urls) == 2
