# tests/services/arcaea/test_songdb.py
"""Arcaea 곡 DB(DoriTran 곡 자료 캐시). 자료는 실제 모양을 줄인 가짜다."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.arcaea.profile import ARCAEA
from services.arcaea.songdb import SONGS_URL, ArcaeaSongDbService

_DATA: dict[str, Any] = {
    "source": "JP Arcaea Wiki",
    "songs": [
        {"id": "x", "title": "[X]", "artist": "Blacklolita", "bpm": 162, "version": "4.3.0", "charts": [
            {"difficulty": "PST", "level": "4", "constant": 4.5, "notes": 594},
            {"difficulty": "FTR", "level": "10", "constant": 10.6, "charter": "東星", "notes": 1190},
        ]},
        {"id": "genesis", "title": "Genesis", "artist": "Iris", "bpm": 120, "version": "1.0.0", "charts": [
            {"difficulty": "FTR", "level": "8", "constant": 8.2, "notes": 600},
        ]},
        {"id": "genesis-chunithm", "title": "Genesis", "artist": "Morrigan feat.Lily", "version": "3.0.0", "charts": [
            {"difficulty": "FTR", "level": "9+", "constant": 9.8},
        ]},
        {"id": "overdead", "title": "overdead.", "artist": "?", "bpm": 213.5, "version": "7.0.1", "charts": [
            {"difficulty": "FTR", "level": "?"},
            {"difficulty": "ZZZ", "level": "1", "constant": 1},
        ]},
    ],
}


class _Http:
    def __init__(self, data: Any = None, error: Exception | None = None) -> None:
        self.data, self.error, self.urls = data, error, []

    async def get_json(self, url: str) -> Any:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.data


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    path = tmp_path / "arcaea" / "songs.json"
    path.parent.mkdir()
    path.write_text(json.dumps(_DATA), encoding="utf-8")
    return path


def test_profile_covers_every_chart_code() -> None:
    assert set(ARCAEA.long_to_short.values()) == set(ARCAEA.chart_codes)
    assert ARCAEA.long_to_short[ARCAEA.default_difficulty_long] == ARCAEA.default_difficulty_code


def test_charts_carry_const_level_notes_and_charter(cache: Path) -> None:
    db = ArcaeaSongDbService(cache)

    song = db.song_by_title("[X]")
    assert song is not None and song.version == "4.0" and song.bpm == 162
    chart = song.charts["FTR"]
    assert (chart.const, chart.level, chart.note_count, chart.designer) == (10.6, "10", 1190, "東星")
    assert db.const_for("[X]", "PST") == 4.5


def test_same_title_songs_are_told_apart_by_artist(cache: Path) -> None:
    db = ArcaeaSongDbService(cache)

    assert db.song_by_title("Genesis") is None
    assert db.const_for("Genesis (Iris)", "FTR") == 8.2
    assert db.const_for("Genesis (Morrigan feat.Lily)", "FTR") == 9.8


def test_charts_without_const_are_kept_as_unknown_and_odd_codes_are_skipped(cache: Path) -> None:
    db = ArcaeaSongDbService(cache)

    song = db.song_by_title("overdead.")
    assert song is not None and set(song.charts) == {"FTR"}
    assert song.charts["FTR"].const is None and song.charts["FTR"].is_const_unknown
    assert db.current_version() == "7.0"


async def test_refresh_replaces_songs_and_writes_the_cache(tmp_path: Path) -> None:
    path = tmp_path / "arcaea" / "songs.json"
    db = ArcaeaSongDbService(path)
    assert db.song_by_title("[X]") is None

    http = _Http(_DATA)
    assert await db.refresh_from_source(http)

    assert http.urls == [SONGS_URL]
    assert db.song_by_title("[X]") is not None
    assert ArcaeaSongDbService(path).song_by_title("[X]") is not None


@pytest.mark.parametrize("bad", [{"songs": []}, [], {"songs": [{"artist": "no title"}]}])
async def test_bad_data_or_network_error_keeps_the_previous_state(cache: Path, bad: Any) -> None:
    db = ArcaeaSongDbService(cache)
    before = cache.read_text(encoding="utf-8")

    assert not await db.refresh_from_source(_Http(bad))
    assert not await db.refresh_from_source(_Http(error=OSError("down")))

    assert db.song_by_title("[X]") is not None
    assert cache.read_text(encoding="utf-8") == before


async def test_refresh_if_due_waits_a_day_after_success_and_an_hour_after_failure(cache: Path) -> None:
    db = ArcaeaSongDbService(cache)
    ok, broken = _Http(_DATA), _Http(error=OSError("down"))

    assert await db.refresh_if_due(ok, now=1000.0)
    assert not await db.refresh_if_due(ok, now=1000.0 + 23 * 3600)
    assert len(ok.urls) == 1

    assert not await db.refresh_if_due(broken, now=1000.0 + 24 * 3600)
    assert not await db.refresh_if_due(broken, now=1000.0 + 24 * 3600 + 1800)
    assert len(broken.urls) == 1
