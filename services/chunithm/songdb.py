from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

import aiosqlite
import yaml

from services.chunithm.profile import CHUNITHM
from services.game.profile import GameProfile
from services.game.songdb import (
    ChartRecord,
    SdvxinMapping,
    SongDb,
    SongRecord,
)

logger = logging.getLogger(__name__)

_SEED_PATH = (
    Path(__file__).parent.parent.parent
    / "third_party" / "chuni_penguin" / "database" / "seeds" / "songs.json"
)
_LINKED_VERSE_PATH = Path(__file__).parent / "data" / "linked_verse.yaml"
_MAX_CANDIDATES = 8

# 기본 게임(츄니즘)의 값. 스킬이 게임을 고르게 되기 전까지 스킬과 표 모듈이 이 이름으로 가져다 쓴다.
VERSION_ORDER: tuple[str, ...] = CHUNITHM.version_order
LONG_TO_SHORT_DIFFICULTY: dict[str, str] = dict(CHUNITHM.long_to_short)



class _HttpGetJson(Protocol):
    async def get_json(self, url: str) -> Any: ...

@dataclass(slots=True, kw_only=True)
class LinkedVerseHit:
    """Linked VERSE 해금곡의 채보 하나와 그 곡이 풀리는 게이트."""

    gate: str
    song: SongRecord
    chart: ChartRecord


@dataclass(slots=True, kw_only=True)
class ChartLinkInfo:
    url: str
    has_mapping: bool

def _load_linked_verse(path: Path) -> list[tuple[str, list[str]]]:
    """(게이트, 곡 제목들)을 파일 순서대로. 이 파일이 Linked VERSE 해금곡의 유일한 출처다."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [(item["gate"], list(item["songs"])) for item in raw]


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
                id=entry["id"],
                charts={},
                bpm=entry.get("bpm"),
                version=entry.get("version"),
                jacket=entry.get("jacket") or None,
            )
            songs[title] = song
        for c in entry.get("charts", []):
            code = c["difficulty"]
            key = (title, code)
            if key in winner_is_available and not (
                entry_available and not winner_is_available[key]
            ):
                continue
            sdvxin_data = c.get("sdvxin")
            song.charts[code] = ChartRecord(
                difficulty=code, const=c.get("const"), note_count=_note_count(c),
                is_available=entry_available, level=c.get("level"),
                sdvxin=(
                    SdvxinMapping(id=str(sdvxin_data["id"]), end_index=sdvxin_data.get("end_index") or "")
                    if sdvxin_data
                    else None
                ),
            )
            winner_is_available[key] = entry_available
    return songs



class SongDbService(SongDb):
    """츄니즘 곡 DB. 시드를 메모리에 한 번 읽고, Chunimaru songdb로 국제판 가용성·신곡 여부·추정 상수 플래그를
    덧씌운다."""

    def __init__(
        self,
        seed_path: Path = _SEED_PATH,
        *,
        conn: aiosqlite.Connection | None = None,
        linked_verse_path: Path = _LINKED_VERSE_PATH,
        profile: GameProfile = CHUNITHM,
    ) -> None:
        # Chunimaru가 아직 한 번도 안 덧씌워졌으면 시드 자신의 available 플래그로
        # 근사한다(완벽하지 않지만 기동 중 네트워크 장애에도 안 죽게 하는 하한선이다).
        raw = json.loads(seed_path.read_text(encoding="utf-8"))
        songs = _load_seed(seed_path)
        super().__init__(
            conn=conn, profile=profile, songs=songs, available={e["title"] for e in raw if e.get("available")}
        )
        self._linked_verse = _load_linked_verse(linked_verse_path)
        self._by_id = {song.id: song for song in self._songs.values()}

    def by_id(self, song_id: int) -> SongRecord | None:
        """즐겨찾기 곡 목록처럼 CHUNITHM-NET의 실제 musicId만 들고 있을 때 곡으로
        되돌린다. 국제판 미수록 곡일 수도 있어 `resolve`와 달리 가용 여부를 거르지 않는다."""
        return self._by_id.get(song_id)

    def current_version(self) -> str | None:
        """지금 가동 중인 버전 — 신곡(`is_new`)인 국제판 곡들의 수록 버전 중 가장 많은 것(2026-10-21 결정:
        설정값을 두지 않고 곡 DB에서 뽑는다). 신곡 표시가 없거나 버전을 모르면 None."""
        counts = Counter(
            song.version for title, song in self._songs.items()
            if song.is_new and song.version and title in self._available_intl
        )
        return counts.most_common(1)[0][0] if counts else None

    def linked_verse_gates(self) -> list[str]:
        """Linked VERSE 게이트 이름을 해금 순서대로."""
        return [gate for gate, _titles in self._linked_verse]

    def find_linked_verse(self, *, gate: str | None = None, difficulty: str | None = None) -> list[LinkedVerseHit]:
        """Linked VERSE 해금곡의 채보를 게이트 순서(같은 게이트 안에서는 파일 순서, 곡 안에서는 난이도 순)로 돌려준다.
        gate는 대소문자를 가리지 않고 게이트 이름과 같아야 한다(모르는 이름이면 ValueError). difficulty를 주면
        그 난이도 하나만 본다. 곡 DB에 없거나 국제판에서 고를 수 없는 곡·채보는 뺀다."""
        wanted = None if gate is None else gate.strip().casefold()
        if wanted is not None and wanted not in {g.casefold() for g in self.linked_verse_gates()}:
            raise ValueError(f"모르는 게이트다: {gate}")
        hits: list[LinkedVerseHit] = []
        for gate_name, titles in self._linked_verse:
            if wanted is not None and gate_name.casefold() != wanted:
                continue
            for title in titles:
                song = self._songs.get(title)
                if song is None or title not in self._available_intl:
                    continue
                codes = [difficulty] if difficulty is not None else [c for c in self._profile.chart_codes if c in song.charts]
                for code in codes:
                    chart = song.charts.get(code)
                    if chart is not None and chart.is_available:
                        hits.append(LinkedVerseHit(gate=gate_name, song=song, chart=chart))
        return hits
    def chart_link(self, song: SongRecord, chart: ChartRecord) -> ChartLinkInfo:
        """채보 뷰어 링크. sdvx.in 매핑이 있으면 그 링크, 없으면 유튜브 검색 링크."""
        if chart.sdvxin is not None:
            from types import SimpleNamespace

            from chuni_penguin.utils.formatting import sdvxin_link

            view = SimpleNamespace(
                id=chart.sdvxin.id, difficulty=chart.difficulty, end_index=chart.sdvxin.end_index
            )
            return ChartLinkInfo(url=sdvxin_link(view), has_mapping=True)

        from urllib.parse import quote

        query = quote(f"{song.title} {chart.difficulty} 譜面確認")
        return ChartLinkInfo(
            url=f"https://www.youtube.com/results?search_query={query}", has_mapping=False
        )

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
        # 시드에 재킷이 없던 곡만 Chunimaru 주소로 채운다(시드 값이 있으면 그대로 둔다).
        new_jackets: dict[str, str] = {}
        new_fallbacks: dict[str, str] = {}
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
                    id=entry.get("idx", -1),
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
                    jacket=entry.get("jacket") or None,
                )
                continue
            if song.jacket is None and entry.get("jacket"):
                new_jackets[title] = entry["jacket"]
            if entry.get("jacket"):
                new_fallbacks[title] = entry["jacket"]
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
        for title, jacket in new_jackets.items():
            self._songs[title].jacket = jacket
        for title, fallback in new_fallbacks.items():
            self._songs[title].jacket_fallback = fallback
        self._songs.update(new_songs)
        self._by_id.update({song.id: song for song in new_songs.values()})
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
