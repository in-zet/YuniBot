from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from core.resolvers import ResolutionError
from core.tool_types import ToolContext

logger = logging.getLogger(__name__)

_SEED_PATH = (
    Path(__file__).parent.parent.parent
    / "third_party" / "chuni_penguin" / "database" / "seeds" / "songs.json"
)
_MAX_CANDIDATES = 8


class _HttpGetJson(Protocol):
    async def get_json(self, url: str) -> Any: ...


@dataclass(slots=True, kw_only=True)
class ChartRecord:
    difficulty: str
    const: float | None
    note_count: int | None
    is_const_unknown: bool = False


@dataclass(slots=True, kw_only=True)
class SongRecord:
    title: str
    aliases: tuple[str, ...]
    artist: str
    charts: dict[str, ChartRecord] = field(default_factory=dict)
    is_new: bool = False


def _note_count(chart: dict[str, Any]) -> int | None:
    total = sum(chart.get(key, 0) or 0 for key in ("tap", "hold", "slide", "air", "flick"))
    return total if total > 0 else None


def _load_seed(seed_path: Path) -> dict[str, SongRecord]:
    """시드 파일을 메모리로 읽는다. 같은 title을 가진 엔트리가 여럿 있을 수 있다
    (흔히 WORLD'S END 채보가 베이스 곡과 분리된 별도 엔트리로 들어있다 — 같은
    title, 다른 id, 서로 겹치지 않는 난이도 코드). 그래서 title이 이미 나온 적
    있으면 새 SongRecord로 덮어쓰지 않고, 기존 SongRecord의 charts에 이번
    엔트리의 charts를 merge한다. artist/aliases는 그 title로 처음 본 엔트리
    것을 그대로 쓴다(이후 엔트리는 chart만 보탠다)."""
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    songs: dict[str, SongRecord] = {}
    for entry in raw:
        charts = {
            c["difficulty"]: ChartRecord(
                difficulty=c["difficulty"], const=c.get("const"), note_count=_note_count(c)
            )
            for c in entry.get("charts", [])
        }
        title = entry["title"]
        existing = songs.get(title)
        if existing is None:
            songs[title] = SongRecord(
                title=title,
                aliases=tuple(entry.get("aliases") or ()),
                artist=entry.get("artist", ""),
                charts=charts,
            )
        else:
            existing.charts.update(charts)
    return songs


class SongDbService:
    """곡·채보 정보, 상수, 노트 수, 별칭, 국제판 수록 여부. 시드를 메모리에 한 번
    읽고, Chunimaru songdb로 국제판 가용성·신곡 여부·추정 상수 플래그를 덧씌운다."""

    def __init__(self, seed_path: Path = _SEED_PATH) -> None:
        self._songs = _load_seed(seed_path)
        # Chunimaru가 아직 한 번도 안 덧씌워졌으면 시드 자신의 available 플래그로
        # 근사한다(완벽하지 않지만 기동 중 네트워크 장애에도 안 죽게 하는 하한선이다).
        raw = json.loads(seed_path.read_text(encoding="utf-8"))
        self._available_intl = {e["title"] for e in raw if e.get("available")}

    async def resolve(self, raw: str, ctx: ToolContext) -> SongRecord:
        text = raw.strip()
        available_songs = [s for title, s in self._songs.items() if title in self._available_intl]

        for song in available_songs:
            if song.title == text:
                return song
        for song in available_songs:
            if text.lower() in (a.lower() for a in song.aliases):
                return song

        lowered = text.lower()
        candidates = [
            song.title
            for song in available_songs
            if lowered in song.title.lower() or any(lowered in a.lower() for a in song.aliases)
        ]
        if candidates:
            raise ResolutionError(candidates=candidates[:_MAX_CANDIDATES])
        raise ResolutionError(message="찾지 못함")

    def const_for(self, title: str, difficulty: str) -> float | None:
        song = self._songs.get(title)
        if song is None:
            return None
        chart = song.charts.get(difficulty)
        return chart.const if chart else None

    def note_count_for(self, title: str, difficulty: str) -> int | None:
        song = self._songs.get(title)
        if song is None:
            return None
        chart = song.charts.get(difficulty)
        return chart.note_count if chart else None

    def _apply_chunimaru(self, data: dict[str, Any]) -> None:
        """Chunimaru songdb 응답(이미 디코딩된 dict)을 로컬 시드에 덧씌운다.
        국제판 가용성은 이 목록으로 완전히 교체한다(시드보다 최신이다).
        네트워크 호출은 refresh_from_chunimaru가 하고, 이 메서드는 순수 머지라
        테스트가 저장된 샘플 JSON으로 직접 부른다."""
        new_available: set[str] = set()
        for entry in data.get("songs", []):
            title = entry["title"]
            new_available.add(title)
            song = self._songs.get(title)
            if song is None:
                continue
            song.is_new = bool(entry.get("isNew", False))
            for code, chart_data in entry.get("charts", {}).items():
                existing = song.charts.get(code)
                const = chart_data.get("const")
                is_const_unknown = bool(chart_data.get("isConstUnknown", False))
                if existing is not None:
                    song.charts[code] = replace(
                        existing, const=const, is_const_unknown=is_const_unknown
                    )
                else:
                    song.charts[code] = ChartRecord(
                        difficulty=code, const=const, note_count=None, is_const_unknown=is_const_unknown
                    )
        self._available_intl = new_available

    async def refresh_from_chunimaru(self, http: _HttpGetJson) -> None:
        """기동 시 한 번(또는 이후 단계의 @schedule이) 부른다. 실패해도 예외를 밖으로
        내지 않는다 — 네트워크가 끊겨도 봇 기동 자체는 막지 않고 시드 기준 availability로
        남는다(00-core.md "켜진 직후" 규칙)."""
        try:
            data = await http.get_json("https://chunimaru.fuwamofu.net/api/songdb/intl")
        except Exception:
            logger.warning("Chunimaru songdb 갱신 실패, 이전 상태를 유지한다", exc_info=True)
            return
        self._apply_chunimaru(data)
