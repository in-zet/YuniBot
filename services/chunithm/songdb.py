from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, cast

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
    # 국제판에서 지금 고를 수 있는 채보인가. 같은 제목의 시드 엔트리 중 일부만
    # available이면, 곡은 찾을 수 있어도 그 채보(흔히 삭제된 WE)는 고를 수 없다.
    is_available: bool = True


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
    것을 그대로 쓴다(이후 엔트리는 chart만 보탠다).

    같은 title에서 같은 난이도 코드가 두 번 나오는 경우도 있다(예: 오래된/삭제된
    WE 채보와 현재 WE 채보가 별도 엔트리로 들어있는 경우). 이때는 파일 순서로
    마지막 것이 이기게 두지 않는다 — 이미 available=true인 엔트리가 채워 넣은
    채보는, available=false인 더 늦은 엔트리가 덮어쓰지 못한다. 반대로
    available=false로 먼저 채워진 채보는 이후 available=true 엔트리가 오면
    덮어쓴다. 둘 다 available=false인 경우만 파일 순서상 먼저 나온 쪽이 남는다."""
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    songs: dict[str, SongRecord] = {}
    # (title, difficulty) -> 현재 그 칸을 채운 엔트리가 available이었는지.
    winner_is_available: dict[tuple[str, str], bool] = {}
    for entry in raw:
        title = entry["title"]
        entry_available = bool(entry.get("available", False))
        song = songs.get(title)
        if song is None:
            song = SongRecord(
                title=title,
                aliases=tuple(entry.get("aliases") or ()),
                artist=entry.get("artist", ""),
                charts={},
            )
            songs[title] = song
        for c in entry.get("charts", []):
            code = c["difficulty"]
            key = (title, code)
            if key in winner_is_available and not (
                entry_available and not winner_is_available[key]
            ):
                continue
            song.charts[code] = ChartRecord(
                difficulty=code, const=c.get("const"), note_count=_note_count(c),
                is_available=entry_available,
            )
            winner_is_available[key] = entry_available
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
        Chunimaru가 알려 준 채보는 국제판에서 고를 수 있는 채보로 본다(is_available=True).
        시드에 없는 곡(벤더링된 시드 이후 추가된 신곡)은 SongRecord를 새로 만든다.

        파싱이 전부 끝나기 전까지는 self._songs/self._available_intl을 건드리지
        않는다 — 응답이 중간에 깨져도 이전 상태가 그대로 남아야 한다. songs가
        비었거나 목록이 아니면 ValueError로 거절한다(빈 응답으로 가용성을 통째로
        지우지 않는다). 네트워크 호출은 refresh_from_chunimaru가 하고, 이 메서드는
        순수 머지라 테스트가 저장된 샘플 JSON으로 직접 부른다."""
        entries = data.get("songs")
        if not isinstance(entries, list) or not entries:
            raise ValueError("Chunimaru 응답에 songs 목록이 없거나 비어 있다")

        new_available: set[str] = set()
        new_is_new: dict[str, bool] = {}
        new_songs: dict[str, SongRecord] = {}
        chart_updates: list[tuple[str, str, ChartRecord]] = []
        for entry in cast("list[dict[str, Any]]", entries):
            title: str = entry["title"]
            new_available.add(title)
            new_is_new[title] = bool(entry.get("isNew", False))
            charts_data: dict[str, dict[str, Any]] = entry.get("charts", {})
            song = self._songs.get(title)
            if song is None:
                # Chunimaru에만 있는 곡 — 시드에 없어도 SongRecord를 새로 만든다.
                # 아니면 SongQuery로 못 찾고, 베스트/신곡 목록에 떠도 상수를 못 찾는다.
                # 별칭과 노트 수는 Chunimaru가 주지 않으므로 비워 둔다(노트 수 없음 →
                # 허용 어택 "계산 불가").
                new_songs[title] = SongRecord(
                    title=title,
                    aliases=(),
                    artist=entry.get("artist", ""),
                    charts={
                        code: ChartRecord(
                            difficulty=code,
                            const=chart_data.get("const"),
                            note_count=None,
                            is_const_unknown=bool(chart_data.get("isConstUnknown", False)),
                            is_available=True,
                        )
                        for code, chart_data in charts_data.items()
                    },
                    is_new=new_is_new[title],
                )
                continue
            for code, chart_data in charts_data.items():
                existing = song.charts.get(code)
                const = chart_data.get("const")
                is_const_unknown = bool(chart_data.get("isConstUnknown", False))
                if existing is not None:
                    updated = replace(
                        existing, const=const, is_const_unknown=is_const_unknown, is_available=True
                    )
                else:
                    updated = ChartRecord(
                        difficulty=code, const=const, note_count=None,
                        is_const_unknown=is_const_unknown, is_available=True,
                    )
                chart_updates.append((title, code, updated))

        # 여기서부터는 실패할 수 없는 대입만 한다.
        for title, code, chart in chart_updates:
            self._songs[title].charts[code] = chart
        for title, is_new in new_is_new.items():
            existing_song = self._songs.get(title)
            if existing_song is not None:
                existing_song.is_new = is_new
        self._songs.update(new_songs)
        self._available_intl = new_available

    async def refresh_from_chunimaru(self, http: _HttpGetJson) -> None:
        """기동 시 한 번(또는 이후 단계의 @schedule이) 부른다. 실패해도 예외를 밖으로
        내지 않는다 — 네트워크가 끊겨도 봇 기동 자체는 막지 않고 시드 기준 availability로
        남는다(00-core.md "켜진 직후" 규칙)."""
        try:
            data = await http.get_json("https://chunimaru.fuwamofu.net/api/songdb/intl")
            self._apply_chunimaru(data)
        except Exception:
            # 네트워크 실패뿐 아니라 응답 형식이 깨진 경우(KeyError/ValueError 등)도
            # 여기서 삼킨다 — _apply_chunimaru는 실패하면 아무것도 바꾸지 않는다.
            logger.warning("Chunimaru songdb 갱신 실패, 이전 상태를 유지한다", exc_info=True)
