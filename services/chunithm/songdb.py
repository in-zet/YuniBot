from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

import aiosqlite

from core.resolvers import ResolutionError
from core.tool_types import ToolContext

logger = logging.getLogger(__name__)

_SEED_PATH = (
    Path(__file__).parent.parent.parent
    / "third_party" / "chuni_penguin" / "database" / "seeds" / "songs.json"
)
_MAX_CANDIDATES = 8

# terms.resolve_difficulty()가 돌려주는 정식 긴 이름("MASTER")을 이 서비스가 쓰는
# 짧은 코드("MAS")로 바꾼다. chart_ref.py도 이 매핑을 그대로 쓴다(중복 정의하지 않는다).
LONG_TO_SHORT_DIFFICULTY: dict[str, str] = {
    "BASIC": "BAS", "ADVANCED": "ADV", "EXPERT": "EXP",
    "MASTER": "MAS", "ULTIMA": "ULT", "WORLD'S END": "WE",
}


class _HttpGetJson(Protocol):
    async def get_json(self, url: str) -> Any: ...


@dataclass(slots=True, kw_only=True)
class SdvxinMapping:
    id: str
    end_index: str = ""


@dataclass(slots=True, kw_only=True)
class ChartRecord:
    difficulty: str
    const: float | None
    note_count: int | None
    is_const_unknown: bool = False
    # 국제판에서 지금 고를 수 있는 채보인가. 같은 제목의 시드 엔트리 중 일부만
    # available이면, 곡은 찾을 수 있어도 그 채보(흔히 삭제된 WE)는 고를 수 없다.
    is_available: bool = True
    level: str | None = None
    sdvxin: SdvxinMapping | None = None


@dataclass(slots=True, kw_only=True)
class SongRecord:
    title: str
    aliases: tuple[str, ...]
    artist: str
    # CHUNITHM-NET의 실제 musicId(즐겨찾기 곡 추가/삭제에 쓴다). 같은 title이 여러 시드
    # 엔트리로 merge될 때(WORLD'S END 분리 엔트리 등) title·artist·aliases와 같은 규칙으로
    # 처음 본 엔트리의 id를 쓴다.
    id: int
    charts: dict[str, ChartRecord] = field(default_factory=dict)
    is_new: bool = False
    bpm: float | None = None
    version: str | None = None


@dataclass(slots=True, kw_only=True)
class ConstRangeHit:
    song: SongRecord
    chart: ChartRecord


@dataclass(slots=True, kw_only=True)
class ChartLinkInfo:
    url: str
    has_mapping: bool


@dataclass(slots=True, kw_only=True)
class AliasRow:
    guild_id: str
    alias: str
    song_title: str
    added_by: str


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


class SongDbService:
    """곡·채보 정보, 상수, 노트 수, 별칭, 국제판 수록 여부. 시드를 메모리에 한 번
    읽고, Chunimaru songdb로 국제판 가용성·신곡 여부·추정 상수 플래그를 덧씌운다."""

    def __init__(self, seed_path: Path = _SEED_PATH, *, conn: aiosqlite.Connection | None = None) -> None:
        self._songs = _load_seed(seed_path)
        # Chunimaru가 아직 한 번도 안 덧씌워졌으면 시드 자신의 available 플래그로
        # 근사한다(완벽하지 않지만 기동 중 네트워크 장애에도 안 죽게 하는 하한선이다).
        raw = json.loads(seed_path.read_text(encoding="utf-8"))
        self._available_intl = {e["title"] for e in raw if e.get("available")}
        self._by_id = {song.id: song for song in self._songs.values()}
        self._conn = conn
        if self._conn is not None:
            self._conn.row_factory = aiosqlite.Row

    def by_id(self, song_id: int) -> SongRecord | None:
        """즐겨찾기 곡 목록처럼 CHUNITHM-NET의 실제 musicId만 들고 있을 때 곡으로
        되돌린다. 국제판 미수록 곡일 수도 있어 `resolve`와 달리 가용 여부를 거르지 않는다."""
        return self._by_id.get(song_id)

    async def resolve(self, raw: str, ctx: ToolContext) -> SongRecord:
        text = raw.strip()
        available_songs = [s for title, s in self._songs.items() if title in self._available_intl]

        for song in available_songs:
            if song.title == text:
                return song

        if self._conn is not None and ctx.guild_id is not None:
            server_alias = await self.get_alias(guild_id=ctx.guild_id, alias=text)
            if server_alias is not None:
                song = self._songs.get(server_alias.song_title)
                if song is not None and song.title in self._available_intl:
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

    async def add_alias(self, *, guild_id: str, alias: str, song_title: str, added_by: str) -> None:
        assert self._conn is not None
        alias = alias.lower()
        existing = await self.get_alias(guild_id=guild_id, alias=alias)
        if existing is not None:
            if existing.song_title != song_title:
                raise ValueError(f"'{alias}'는 이미 '{existing.song_title}'에 쓰인 별칭이다")
            # 같은 곡으로 다시 등록하는 멱등 호출이다 — 아무것도 바꾸지 않는다. 원래
            # 소유자(added_by)/시각(created_at)을 덮어쓰면 누구나 재등록만으로 다른
            # 사람의 별칭 소유권을 뺏고, alias_remove의 "추가한 사람만 지운다" 제한을
            # 돌아갈 수 있다.
            return

        # 다른 곡의 정식 제목이나 전역(시드) 별칭과 겹치는 이름은 서버 별칭으로 등록할 수
        # 없다 — resolve()가 서버 별칭을 전역 별칭보다 먼저 보므로, 등록을 허락하면 그 뒤로
        # 전역 별칭으로 찾던 곡 대신 전혀 다른 곡을 돌려주게 된다.
        conflict_title = self._find_global_name_conflict(alias, song_title)
        if conflict_title is not None:
            raise ValueError(f"'{alias}'는 이미 '{conflict_title}'에 쓰인 이름이다")

        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO songdb_aliases (guild_id, alias, song_title, added_by, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (guild_id, alias, song_title, added_by, now),
        )
        await self._conn.commit()

    def _find_global_name_conflict(self, alias_lower: str, song_title: str) -> str | None:
        """alias_lower(이미 소문자)가 song_title이 아닌 다른 곡의 정식 제목이나 전역(시드)
        별칭과 겹치면 그 다른 곡의 제목을 돌려준다(없으면 None). resolve()의 정식 제목/
        전역 별칭 티어가 보는 것과 같은 대상(available_songs)만 본다 — 국제판에 없는 곡의
        이름은 resolve()가 그 티어에서 아예 보지 않으므로 실제로 겹칠 위험이 없다.
        같은 곡 자신의 제목/별칭과 겹치는 것은 충돌로 보지 않는다(허용)."""
        for title, song in self._songs.items():
            if title == song_title or title not in self._available_intl:
                continue
            if title.lower() == alias_lower or alias_lower in (a.lower() for a in song.aliases):
                return title
        return None

    async def get_alias(self, *, guild_id: str, alias: str) -> AliasRow | None:
        assert self._conn is not None
        alias = alias.lower()
        cursor = await self._conn.execute(
            "SELECT guild_id, alias, song_title, added_by FROM songdb_aliases WHERE guild_id = ? AND alias = ?",
            (guild_id, alias),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return AliasRow(guild_id=row["guild_id"], alias=row["alias"], song_title=row["song_title"], added_by=row["added_by"])

    async def remove_alias(self, *, guild_id: str, alias: str) -> None:
        assert self._conn is not None
        alias = alias.lower()
        await self._conn.execute("DELETE FROM songdb_aliases WHERE guild_id = ? AND alias = ?", (guild_id, alias))
        await self._conn.commit()

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

    def is_new_for(self, title: str) -> bool:
        """그 곡이 신곡(신곡20 프레임 대상)인가. 모르는 곡은 False — Chunimaru 덧씌우기가
        한 번도 안 됐으면 시드에는 신곡 표시가 없으므로 전부 False다."""
        song = self._songs.get(title)
        return song.is_new if song is not None else False

    def find_by_const(
        self, *, min_const: float, max_const: float, difficulty: str | None = None
    ) -> list[ConstRangeHit]:
        """상수 범위(양끝 포함)로, 지금 국제판에서 고를 수 있는 채보를 상수 오름차순(같으면
        제목순)으로 찾는다. difficulty는 짧은 코드("MAS" 등)다. 비우면 난이도를 가리지 않고
        그 상수 범위에 드는 채보를 전부 찾는다(2026-10-18 결정 — 상수는 채보 단위로 이미
        유일하게 정해지므로 난이도로 좁힐 필요가 없다는 실기 피드백)."""
        hits: list[ConstRangeHit] = []
        for title, song in self._songs.items():
            if title not in self._available_intl:
                continue
            codes = [difficulty] if difficulty is not None else list(song.charts)
            for code in codes:
                chart = song.charts.get(code)
                if chart is None or not chart.is_available or chart.const is None:
                    continue
                if min_const <= chart.const <= max_const:
                    hits.append(ConstRangeHit(song=song, chart=chart))
        hits.sort(key=lambda hit: (hit.chart.const, hit.song.title))
        return hits

    def find_by_level(self, *, level: str, difficulty: str | None = None) -> list[ConstRangeHit]:
        """표기 레벨(문자열 그대로 비교, 예: "14+")로, 지금 국제판에서 고를 수 있는 채보를
        찾는다. find_by_const와 같은 가용성 규칙·difficulty 생략 의미(전체 난이도)·정렬을
        쓴다 — 상수를 모르는 채보는 뒤로 보낸다(표시에는 상수도 같이 쓸 수 있으니 숨기지
        않는다)."""
        hits: list[ConstRangeHit] = []
        for title, song in self._songs.items():
            if title not in self._available_intl:
                continue
            codes = [difficulty] if difficulty is not None else list(song.charts)
            for code in codes:
                chart = song.charts.get(code)
                if chart is None or not chart.is_available or chart.level != level:
                    continue
                hits.append(ConstRangeHit(song=song, chart=chart))
        hits.sort(key=lambda hit: (hit.chart.const is None, hit.chart.const or 0.0, hit.song.title))
        return hits

    def find_by_version(self, *, version: str, difficulty: str | None = None) -> list[ConstRangeHit]:
        """수록 버전으로 채보를 찾는다. difficulty를 주면 find_by_const처럼 그 난이도 하나만
        본다. difficulty를 비우면("버전 전체" 폴더) 그 버전 곡들의 **고를 수 있는 채보를
        전부**(난이도마다 하나씩) 돌려준다 — 폴더 목표의 "그 버전 전체"는 특정 난이도 하나로
        좁힐 이유가 설계서에 없다(docs/design/skills/chunithm-goals.md "폴더 정의"는 난이도를
        조합한다고만 적었지, 버전 폴더에서 난이도가 필수라고 하지 않았다)."""
        hits: list[ConstRangeHit] = []
        for title, song in self._songs.items():
            if title not in self._available_intl or song.version != version:
                continue
            codes = [difficulty] if difficulty is not None else list(song.charts)
            for code in codes:
                chart = song.charts.get(code)
                if chart is None or not chart.is_available:
                    continue
                hits.append(ConstRangeHit(song=song, chart=chart))
        hits.sort(key=lambda hit: (hit.chart.const is None, hit.chart.const or 0.0, hit.song.title))
        return hits

    def find_by_artist(self, *, name: str) -> list[SongRecord]:
        """작곡가(아티스트 크레딧) 이름으로, 지금 국제판에서 고를 수 있는 곡을 제목순으로
        찾는다. 대소문자 구분 없는 부분 일치다 — "A vs. B" 같은 콜라보 크레딧에도 걸리게
        한다. 상수·레벨·버전과 달리 아티스트는 채보가 아니라 곡 단위 값이라 ConstRangeHit이
        아니라 SongRecord를 그대로 돌려준다."""
        lowered = name.lower()
        songs = [
            song for title, song in self._songs.items()
            if title in self._available_intl and lowered in song.artist.lower()
        ]
        songs.sort(key=lambda song: song.title)
        return songs

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
