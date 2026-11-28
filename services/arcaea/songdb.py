# services/arcaea/songdb.py
"""Arcaea 곡 DB. 공식 곡·상수 목록이 없어 비공식 자료 DoriTran/arcaea-chart-constant-wiki(MIT, 일본 위키
wikiwiki.jp/arcaea를 긁은 것)의 `songs.json`을 `data/arcaea/`에 캐시해 두고 읽는다(저장소에는 넣지 않는다).

2026-11-28 Arcaea Online 베스트 50의 곡별 포텐셜과 대조해 상수가 있는 41채보 중 40개가 맞았다. 상수가 빈 채보는 대부분
만우절·숨김 곡(레벨 "-"·"?")이다. 같은 제목의 다른 곡(Genesis, Quon)은 제목 뒤에 작곡가를 붙여 가른다.

자료에 없는 새 곡·바뀐 상수는 동기화 때 Arcaea Online 베스트 50에서 거꾸로 낸 상수(`learn_consts`, 테이블
`arcaea_const_observed`)로 덮는다."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from services.arcaea.profile import ARCAEA
from services.game.songdb import ChartRecord, SongDb, SongRecord

logger = logging.getLogger(__name__)

SONGS_URL = "https://raw.githubusercontent.com/DoriTran/arcaea-chart-constant-wiki/main/public/data/songs.json"
_REFRESH_INTERVAL_SECONDS = 24 * 3600
_RETRY_INTERVAL_SECONDS = 3600


class _HttpGetJson(Protocol):
    async def get_json(self, url: str) -> Any: ...


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _major_version(version: str) -> str | None:
    """"6.11.2" → "6.0"(곡이 들어온 큰 버전). 모르는 모양이면 None."""
    head = version.split(".", 1)[0]
    return f"{head}.0" if head.isdigit() else None


def _charts_of(entry: dict[str, Any]) -> dict[str, ChartRecord]:
    charts: dict[str, ChartRecord] = {}
    for chart in entry.get("charts") or []:
        code = _text(chart.get("difficulty")) if isinstance(chart, dict) else ""
        if code not in ARCAEA.chart_codes:
            continue
        const = _number(chart.get("constant"))
        notes = _number(chart.get("notes"))
        charts[code] = ChartRecord(
            difficulty=code,
            const=const,
            # 만우절·숨김 곡은 레벨이 "-"·"?"이고 상수가 없다. 레벨만 아는 채보도 상수 모름으로 둔다.
            is_const_unknown=const is None,
            note_count=int(notes) if notes else None,
            level=_text(chart.get("level")) or None,
            designer=_text(chart.get("charter")) or None,
        )
    return charts


def _entries_from(data: Any) -> list[dict[str, Any]]:
    songs = data.get("songs") if isinstance(data, dict) else None  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(songs, list) or not songs:
        raise ValueError("Arcaea 곡 자료에 곡 목록이 없다")
    entries: list[dict[str, Any]] = []
    for item in songs:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(item, dict) or not _text(item.get("title")):  # pyright: ignore[reportUnknownMemberType]
            raise ValueError("Arcaea 곡 자료 항목에 title이 없다")
        entries.append(item)  # pyright: ignore[reportUnknownArgumentType]
    return entries


def _load(entries: list[dict[str, Any]]) -> dict[str, SongRecord]:
    """곡 자료 → 제목별 곡. 제목이 겹치는 곡은 "제목 (작곡가)"로 둘 다 바꾼다(어느 쪽이 원래 제목인지 정할 근거가 없다)."""
    repeated = {title for title, count in Counter(_text(e["title"]) for e in entries).items() if count > 1}
    songs: dict[str, SongRecord] = {}
    for index, entry in enumerate(entries, start=1):
        title = _text(entry["title"])
        artist = _text(entry.get("artist"))
        if title in repeated:
            title = f"{title} ({artist})"
        if title in songs:
            logger.warning("Arcaea 곡 자료에 같은 곡이 두 번 있다, 처음 것만 쓴다: %s", title)
            continue
        songs[title] = SongRecord(
            title=title,
            aliases=(),
            artist=artist,
            id=index,
            charts=_charts_of(entry),
            bpm=_number(entry.get("bpm")),
            version=_major_version(_text(entry.get("version"))),
            # 일본 위키의 재킷 그림 주소(사용자 결정 2026-11-28 — 곡 전부에 있다). 받은 그림은 ImageCache가 디스크에 둔다.
            jacket=_text(entry.get("jacket")) or None,
            pack=_text(entry.get("pack")) or None,
        )
    return songs


def _write_cache(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


_ASSET_BASE = "https://webassets.lowiro.com"


def official_jacket_url(bg: str) -> str:
    """Arcaea Online 공식 재킷(사이트 코드와 같은 주소, 2026-11-28 받아지는 것 확인)."""
    return f"{_ASSET_BASE}/{bg}.jpg"


def character_icon_url(icon: str) -> str:
    """Arcaea Online 프로필의 캐릭터 그림(사이트 코드의 `getCharacterDiamondImageAsset`)."""
    return f"{_ASSET_BASE}/chr/{icon}.png"


# 물결표 모양 차이(2026-11-28 실기: Arcaea Online은 「…たべたいよ〜」(U+301C), 곡 자료는 「…よ～」(U+FF5E)라 기록이 곡에
# 안 이어졌다). NFKC는 U+FF5E만 "~"로 바꾸므로 나머지도 같게 본다.
_WAVES = str.maketrans({"\u301c": "~", "\u3030": "~", "\u2053": "~", "\u223c": "~"})


def title_key(text: str) -> str:
    """제목 비교용 글: 전각·반각(NFKC)·대소문자·띄어쓰기·물결표 모양을 무시한다."""
    return "".join(unicodedata.normalize("NFKC", text).translate(_WAVES).casefold().split())


@dataclass(slots=True, kw_only=True, frozen=True)
class ObservedConst:
    """Arcaea Online이 준 곡 포텐셜에서 거꾸로 낸 상수 하나."""

    song_title: str
    difficulty: str
    const: float
    artist: str = ""


class ArcaeaSongDbService(SongDb):
    """캐시 파일이 있으면 그것으로 시작하고, 하루에 한 번 새로 받아 캐시와 메모리를 함께 바꾼다. 캐시가 없고 아직 못
    받았으면 곡이 비어 있다. Arcaea에는 신곡 틀이 없어 신곡 표시는 늘 꺼져 있다."""

    def __init__(self, cache_path: Path, *, conn: aiosqlite.Connection | None = None) -> None:
        super().__init__(conn=conn, profile=ARCAEA, songs={}, available=set())
        self._cache_path = cache_path
        self._next_refresh_at = 0.0
        self._observed: dict[tuple[str, str], ObservedConst] = {}
        self._title_index: dict[str, list[SongRecord]] = {}
        self._art: dict[str, str] = {}  # 곡 제목 → 공식 재킷 번호(bg)
        if cache_path.is_file():
            try:
                self._adopt(_load(_entries_from(json.loads(cache_path.read_text(encoding="utf-8")))))
            except (OSError, ValueError, KeyError):
                logger.warning("Arcaea 곡 DB 캐시를 읽지 못했다, 빈 상태로 시작한다", exc_info=True)

    def _adopt(self, songs: dict[str, SongRecord]) -> None:
        self._songs = songs
        self._apply_observed()

    def _apply_observed(self) -> None:
        """거꾸로 낸 상수를 곡 DB에 덮는다. 곡 DB에 없는 곡·채보는 그 상수만 가진 곡·채보로 더한다."""
        for observed in self._observed.values():
            song = self._songs.get(observed.song_title)
            if song is None:
                song = SongRecord(title=observed.song_title, aliases=(), artist=observed.artist, id=-1)
                self._songs[observed.song_title] = song
            chart = song.charts.get(observed.difficulty)
            song.charts[observed.difficulty] = (
                ChartRecord(difficulty=observed.difficulty, const=observed.const, note_count=None)
                if chart is None
                else replace(chart, const=observed.const, is_const_unknown=False)
            )
        for title, bg in self._art.items():
            song = self._songs.get(title)
            if song is None:
                continue
            url = official_jacket_url(bg)
            if song.jacket is None or song.jacket == url:
                song.jacket = url
            else:
                song.jacket_fallback = url  # 위키 재킷을 못 받으면 공식 재킷으로
        self._available_intl = set(self._songs)
        self._title_index = {}
        for song in self._songs.values():
            suffix = f" ({song.artist})"
            base = song.title.removesuffix(suffix) if song.artist else song.title  # 같은 제목 곡은 작곡가를 붙여 뒀다
            self._title_index.setdefault(title_key(base), []).append(song)

    async def load_observed(self) -> None:
        """저장해 둔 거꾸로 낸 상수를 읽어 덮는다(봇이 켜질 때 한 번)."""
        if self._conn is None:
            return
        cursor = await self._conn.execute("SELECT song_title, difficulty, const, artist FROM arcaea_const_observed")
        for row in await cursor.fetchall():
            self._observed[(row[0], row[1])] = ObservedConst(
                song_title=row[0], difficulty=row[1], const=row[2], artist=row[3] or ""
            )
        cursor = await self._conn.execute("SELECT song_title, bg FROM arcaea_song_art")
        self._art = {row[0]: row[1] for row in await cursor.fetchall()}
        self._apply_observed()

    async def learn_art(self, art: dict[str, str]) -> None:
        """동기화가 받은 곡별 공식 재킷 번호를 저장하고 곡 DB에 붙인다(바뀐 것만 저장)."""
        changed = {title: bg for title, bg in art.items() if bg and self._art.get(title) != bg}
        if not changed:
            return
        self._art.update(changed)
        if self._conn is not None:
            now = datetime.now(UTC).isoformat()
            await self._conn.executemany(
                "INSERT INTO arcaea_song_art (song_title, bg, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (song_title) DO UPDATE SET bg = excluded.bg, updated_at = excluded.updated_at",
                [(title, bg, now) for title, bg in changed.items()],
            )
            await self._conn.commit()
        self._apply_observed()

    async def learn_consts(self, observed: Sequence[ObservedConst]) -> int:
        """동기화가 거꾸로 낸 상수를 저장하고 덮는다. 곡 DB와 같은 값이면 저장하지 않는다. 바꾼 채보 수를 돌려준다."""
        changed = [
            item for item in observed
            if self.const_for(item.song_title, item.difficulty) != item.const
        ]
        if not changed:
            return 0
        for item in changed:
            logger.info(
                "Arcaea 상수 보정: %s %s %s → %s", item.song_title, item.difficulty,
                self.const_for(item.song_title, item.difficulty), item.const,
            )
            self._observed[(item.song_title, item.difficulty)] = item
        if self._conn is not None:
            now = datetime.now(UTC).isoformat()
            await self._conn.executemany(
                "INSERT INTO arcaea_const_observed (song_title, difficulty, const, artist, observed_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (song_title, difficulty) DO UPDATE SET const = excluded.const, "
                "artist = excluded.artist, observed_at = excluded.observed_at",
                [(item.song_title, item.difficulty, item.const, item.artist, now) for item in changed],
            )
            await self._conn.commit()
        self._apply_observed()
        return len(changed)

    def title_for(self, titles: Sequence[str], artist: str) -> str:
        """Arcaea Online의 제목들(일본어·영어) → 곡 DB 제목. 같은 제목 곡이 둘이면 작곡가로 고른다. 곡 DB에 없으면 앞의
        제목(일본어) 그대로다 — 곡 DB(일본 위키)가 원제를 쓰기 때문이다."""
        for title in titles:
            matches = self._title_index.get(title_key(title), [])
            if len(matches) == 1:
                return matches[0].title
            if matches:
                same_artist = [song for song in matches if title_key(song.artist) == title_key(artist)]
                if len(same_artist) == 1:
                    return same_artist[0].title
        return next((title for title in titles if title), "")

    def current_version(self) -> str | None:
        versions = [song.version for song in self._songs.values() if song.version in ARCAEA.version_order]
        return max(versions, key=ARCAEA.version_order.index, default=None)

    async def refresh_on_startup(self, http: _HttpGetJson) -> None:
        _ = await self.refresh_if_due(http, now=time.time())

    async def refresh_periodic(self, http: _HttpGetJson, *, now: float) -> None:
        _ = await self.refresh_if_due(http, now=now)

    async def refresh_if_due(self, http: _HttpGetJson, *, now: float) -> bool:
        """받은 지 하루가 안 됐으면 아무것도 안 한다. 실패하면 한 시간 뒤에 다시 시도한다. 받았으면 True."""
        if now < self._next_refresh_at:
            return False
        refreshed = await self.refresh_from_source(http)
        self._next_refresh_at = now + (_REFRESH_INTERVAL_SECONDS if refreshed else _RETRY_INTERVAL_SECONDS)
        return refreshed

    async def refresh_from_source(self, http: _HttpGetJson) -> bool:
        """곡 자료를 받아 메모리와 캐시 파일을 바꾼다. 실패하면(네트워크, 형식 깨짐) 아무것도 바꾸지 않고 False — 봇 기동을
        막지 않는다."""
        try:
            data = await http.get_json(SONGS_URL)
            songs = _load(_entries_from(data))
            await asyncio.to_thread(_write_cache, self._cache_path, data)
        except Exception:
            logger.warning("Arcaea 곡 자료 갱신 실패, 이전 상태를 유지한다", exc_info=True)
            return False
        self._adopt(songs)
        return True
