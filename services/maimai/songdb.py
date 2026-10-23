# services/maimai/songdb.py
"""maimai DX 국제판 곡 DB. otoge-db `music-ex-intl.json`을 `data/`에 캐시해 두고 읽는다(저장소에는 넣지 않는다).

otoge-db는 곡 하나에 STD(`lev_*`)와 DX(`dx_lev_*`) 채보를 함께 담고, 宴(UTAGE)은 `[宴]`·`[協]`처럼 한자 접두사가
붙은 별도 곡(`lev_utage`)으로 둔다. 이 모듈이 그것을 `SongRecord`/`ChartRecord`로 바꾼다."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiosqlite

from services.game.songdb import ChartRecord, SongDb, SongRecord, chart_key
from services.maimai.profile import MAIMAI, VERSION_NAMES

logger = logging.getLogger(__name__)

OTOGE_URL = "https://otoge-db.net/maimai/data/music-ex-intl.json"
# 재킷 파일 이름(`image_url`) 앞에 붙이는 주소. 2026-10-23 두 파일로 HTTP 200 확인.
JACKET_BASE = "https://otoge-db.net/maimai/jacket"

# 宴은 NET에서 STD/DX 어느 쪽으로 나오는지 아직 모른다(Part 3 실측 뒤 확정). 그때까지 STD로 둔다.
_UTAGE_CHART_TYPE = "STD"

# (otoge-db 칸 이름 조각, 난이도 코드). 낮은 쪽부터.
_DIFFICULTIES = (("bas", "BAS"), ("adv", "ADV"), ("exp", "EXP"), ("mas", "MAS"), ("remas", "REM"))
# (otoge-db 칸 앞머리, 채보 종류)
_CHART_TYPES = (("lev_", "STD"), ("dx_lev_", "DX"))

_PREFIX_TO_NAME = VERSION_NAMES
_NAME_TO_PREFIX = {name: prefix for prefix, name in VERSION_NAMES.items()}


class _HttpGetJson(Protocol):
    async def get_json(self, url: str) -> Any: ...


@dataclass(slots=True, kw_only=True)
class _Loaded:
    songs: dict[str, SongRecord]
    available: set[str]
    # 곡 제목 → 버전 코드 앞 세 자리. 현 버전을 덮어쓸 때 신곡 표시를 다시 계산하려고 들고 있다.
    prefix_of: dict[str, str]
    latest_prefix: str | None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _float(value: Any) -> float | None:
    try:
        return float(_text(value))
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(_text(value))
    except ValueError:
        return None


def _charts_of(entry: dict[str, Any], *, available: bool) -> dict[str, ChartRecord]:
    charts: dict[str, ChartRecord] = {}
    for head, chart_type in _CHART_TYPES:
        for part, code in _DIFFICULTIES:
            level = _text(entry.get(f"{head}{part}"))
            if not level:
                continue
            const = _float(entry.get(f"{head}{part}_i"))
            charts[chart_key(chart_type, code)] = ChartRecord(
                difficulty=code,
                chart_type=chart_type,
                const=const,
                # 상수 칸이 빈 채보가 있다(2026-10-23 기준 10개). 레벨만 알고 상수는 모른다.
                is_const_unknown=const is None,
                note_count=_int(entry.get(f"{head}{part}_notes")),
                is_available=available,
                level=level,
                designer=_text(entry.get(f"{head}{part}_designer")) or None,
            )
    utage = _text(entry.get("lev_utage"))
    if utage:
        charts[chart_key(_UTAGE_CHART_TYPE, "UTA")] = ChartRecord(
            difficulty="UTA",
            chart_type=_UTAGE_CHART_TYPE,
            const=None,
            note_count=_int(entry.get("lev_utage_notes")),
            is_available=available,
            # 宴의 레벨은 "13+?"처럼 추정 표시 물음표가 붙어 있다.
            level=utage.rstrip("?"),
            designer=_text(entry.get("lev_utage_designer")) or None,
        )
    return charts


def _load(entries: list[dict[str, Any]], *, override_prefix: str | None = None) -> _Loaded:
    """otoge-db 항목들을 곡 목록으로 바꾼다. 같은 제목이 여러 항목이면(`Link` 두 곡, 宴 한 곡의 난이도별 항목)
    츄니즘 시드처럼 처음 본 항목의 곡 정보를 쓰고 채보만 보탠다. 같은 칸이 겹치면 먼저 나온 것이 남는다."""
    songs: dict[str, SongRecord] = {}
    available: set[str] = set()
    prefix_of: dict[str, str] = {}
    for entry in entries:
        title = entry["title"]
        in_intl = _text(entry.get("intl")) == "1"
        code = _text(entry.get("version"))
        if in_intl:
            available.add(title)
        song = songs.get(title)
        if song is None:
            prefix = code[:3]
            prefix_of[title] = prefix
            image = _text(entry.get("image_url"))
            song = SongRecord(
                title=title,
                aliases=(),
                artist=_text(entry.get("artist")),
                id=_int(entry.get("sort")) or -1,
                bpm=_float(entry.get("bpm")),
                version=_PREFIX_TO_NAME.get(prefix, prefix),
                jacket=f"{JACKET_BASE}/{image}" if image else None,
            )
            songs[title] = song
        for key, chart in _charts_of(entry, available=in_intl).items():
            song.charts.setdefault(key, chart)
    # 현 버전 = 국제판 수록곡 중 가장 큰 버전. 신곡은 그 버전의 곡이다.
    latest = max((prefix_of[title] for title in available), default=None)
    current = override_prefix or latest
    for title, song in songs.items():
        song.is_new = current is not None and prefix_of[title] == current
    return _Loaded(songs=songs, available=available, prefix_of=prefix_of, latest_prefix=latest)


def _entries_from(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list) or not data:
        raise ValueError("otoge-db 응답이 비어 있거나 목록이 아니다")
    entries: list[dict[str, Any]] = []
    for item in data:  # pyright: ignore[reportUnknownVariableType]
        # 제목은 다듬지 않는다 — 전각 공백 하나("　")가 제목인 실제 곡이 있다(2026-10-23).
        if not isinstance(item, dict) or not item.get("title") or not _text(item.get("version")):  # pyright: ignore[reportUnknownMemberType]
            raise ValueError("otoge-db 항목에 title/version이 없다")
        entries.append(item)  # pyright: ignore[reportUnknownArgumentType]
    return entries


def _write_cache(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class MaimaiSongDbService(SongDb):
    """maimai DX 국제판 곡·채보 정보. 캐시 파일이 있으면 그것으로 시작하고, `refresh_from_otoge`가 새로 받아
    캐시와 메모리를 함께 바꾼다. 캐시가 없고 아직 못 받았으면 곡이 비어 있다."""

    def __init__(
        self,
        cache_path: Path,
        *,
        conn: aiosqlite.Connection | None = None,
        current_version: str | None = None,
    ) -> None:
        super().__init__(conn=conn, profile=MAIMAI, songs={}, available=set())
        self._cache_path = cache_path
        self._prefix_of: dict[str, str] = {}
        self._latest_prefix: str | None = None
        self._override_prefix: str | None = None
        self.set_current_version(current_version)
        if cache_path.is_file():
            try:
                entries = _entries_from(json.loads(cache_path.read_text(encoding="utf-8")))
                self._adopt(_load(entries, override_prefix=self._override_prefix))
            except (OSError, ValueError, KeyError):
                logger.warning("maimai 곡 DB 캐시를 읽지 못했다, 빈 상태로 시작한다", exc_info=True)

    def _adopt(self, loaded: _Loaded) -> None:
        self._songs = loaded.songs
        self._available_intl = loaded.available
        self._prefix_of = loaded.prefix_of
        self._latest_prefix = loaded.latest_prefix

    def set_current_version(self, name: str | None) -> None:
        """관리자가 현 버전을 덮어쓴다(None이면 곡 DB에서 뽑은 값으로 돌아간다). 신곡 표시를 다시 계산한다.
        모르는 버전 이름은 ValueError다."""
        if name is not None and name not in _NAME_TO_PREFIX:
            raise ValueError(f"모르는 maimai 버전이다: {name}")
        self._override_prefix = _NAME_TO_PREFIX[name] if name is not None else None
        current = self._override_prefix or self._latest_prefix
        for title, song in self._songs.items():
            song.is_new = current is not None and self._prefix_of[title] == current

    def current_version(self) -> str | None:
        """지금 가동 중인 버전 이름. 덮어쓴 값이 있으면 그것, 없으면 국제판 수록곡 중 가장 큰 버전. 곡이 없으면 None."""
        prefix = self._override_prefix or self._latest_prefix
        return None if prefix is None else _PREFIX_TO_NAME.get(prefix, prefix)

    async def refresh_from_otoge(self, http: _HttpGetJson) -> bool:
        """otoge-db를 받아 메모리와 캐시 파일을 바꾼다. 실패하면(네트워크, 형식 깨짐) 아무것도 바꾸지 않고
        False를 돌려준다 — 봇 기동을 막지 않는다."""
        try:
            data = await http.get_json(OTOGE_URL)
            loaded = _load(_entries_from(data), override_prefix=self._override_prefix)
            await asyncio.to_thread(_write_cache, self._cache_path, data)
        except Exception:
            logger.warning("otoge-db 갱신 실패, 이전 상태를 유지한다", exc_info=True)
            return False
        self._adopt(loaded)
        return True
