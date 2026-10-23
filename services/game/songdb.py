# services/game/songdb.py
"""곡 DB의 게임 공통 부분: 곡·채보 자료형, 곡 찾기(제목·별칭·상수·레벨·버전·난이도·노트 수), 별칭 저장.
곡 목록을 어디서 읽어 오는지(츄니즘 시드+츄니마루, maimai otoge-db)는 하위 클래스가 맡는다."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiosqlite

from core.fuzzy import similar_names
from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.game.profile import GameProfile

_MAX_CANDIDATES = 8


def _difficulty_range(
    low: str | None, high: str | None, order: tuple[str, ...]
) -> frozenset[str] | None:
    """난이도 범위가 걸린 코드들. 범위를 안 걸었으면 None(가리지 않는다). 낮은 쪽이 높은 쪽보다 높으면 빈 집합이다."""
    if low is None and high is None:
        return None
    for code in (low, high):
        if code is not None and code not in order:
            raise ValueError(f"난이도 범위는 {order[0]}~{order[-1]} 안에서만 줄 수 있다: {code}")
    start = order.index(low) if low is not None else 0
    end = order.index(high) if high is not None else len(order) - 1
    return frozenset(order[start : end + 1])


def _const_descending(hit: ConstRangeHit) -> tuple[bool, float, str]:
    """검색 결과의 고정 순서: 상수 큰 것부터, 상수를 모르는 채보는 뒤, 같으면 제목순(2026-10-22 — 사용자는 상수·난이도·
    레이팅을 방향 말이 없으면 큰 쪽부터 본다)."""
    return (hit.chart.const is None, -(hit.chart.const or 0.0), hit.song.title)


def chart_key(chart_type: str, difficulty: str) -> str:
    """`SongRecord.charts`의 키. 채보 종류가 없는 게임(츄니즘)은 난이도 코드 그대로("MAS"), 있는 게임은
    "DX:MAS"처럼 종류:난이도다."""
    return f"{chart_type}:{difficulty}" if chart_type else difficulty


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
    # 같은 곡에 채보 종류가 둘 이상인 게임(maimai STD/DX)의 종류. 츄니즘은 ''.
    chart_type: str = ""
    designer: str | None = None


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
    # 재킷 그림. 시드의 재킷 파일 이름이거나, 시드에 없던 곡이면 Chunimaru가 준 전체 주소.
    # 주소로 바꾸는 일은 `image_cache.jacket_url`이 한다.
    jacket: str | None = None
    # Chunimaru가 준 재킷 전체 주소. CHUNITHM-NET에서 못 받을 때(사이트 장애 등) 대신 받는다.
    jacket_fallback: str | None = None


def _select_charts(song: SongRecord, codes: list[str] | None, chart_type: str | None) -> list[ChartRecord]:
    """곡의 채보 중 난이도 코드(여럿이면 준 순서대로, None이면 전부)와 채보 종류(None이면 가리지 않음)에 맞는 것."""
    charts = list(song.charts.values())
    if chart_type is not None:
        charts = [chart for chart in charts if chart.chart_type == chart_type]
    if codes is None:
        return charts
    return [chart for code in codes for chart in charts if chart.difficulty == code]


@dataclass(slots=True, kw_only=True)
class ConstRangeHit:
    song: SongRecord
    chart: ChartRecord


@dataclass(slots=True, kw_only=True)
class AliasRow:
    alias: str
    song_title: str
    added_by: str


class SongDb:
    """곡·채보 정보, 상수, 노트 수, 별칭, 수록 여부를 메모리에서 찾는 공통 틀. 하위 클래스가 `_songs`
    (제목 → 곡)와 `_available_intl`(국제판에서 고를 수 있는 곡 제목)을 채운다."""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection | None,
        profile: GameProfile,
        songs: dict[str, SongRecord],
        available: set[str],
    ) -> None:
        self._profile = profile
        self._songs = songs
        self._available_intl = available
        self._conn = conn
        if self._conn is not None:
            self._conn.row_factory = aiosqlite.Row

    @property
    def profile(self) -> GameProfile:
        return self._profile
    async def resolve(self, raw: str, ctx: ToolContext) -> SongRecord:
        text = raw.strip()
        available_songs = [s for title, s in self._songs.items() if title in self._available_intl]

        for song in available_songs:
            if song.title == text:
                return song

        if self._conn is not None:
            server_alias = await self.get_alias(alias=text)
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
        # 부분 일치가 없으면 글자가 비슷한 제목을 후보로 준다(모델이 "이 곡 맞아?"하고 되묻는 재료).
        similar = similar_names(text, (song.title for song in available_songs), limit=_MAX_CANDIDATES, cutoff=0.6)
        if similar:
            raise ResolutionError(candidates=similar)
        raise ResolutionError(message="찾지 못함")

    async def add_alias(self, *, alias: str, song_title: str, added_by: str) -> None:
        assert self._conn is not None
        alias = alias.lower()
        existing = await self.get_alias(alias=alias)
        if existing is not None:
            if existing.song_title != song_title:
                raise ValueError(f"'{alias}'는 이미 '{existing.song_title}'에 쓰인 별칭이다")
            # 같은 곡으로 다시 등록하는 멱등 호출이다 — 아무것도 바꾸지 않는다. 원래
            # 소유자(added_by)/시각(created_at)을 덮어쓰면 누구나 재등록만으로 다른
            # 사람의 별칭 소유권을 뺏고, alias_remove의 "추가한 사람만 지운다" 제한을
            # 돌아갈 수 있다.
            return

        # 다른 곡의 정식 제목이나 전역(시드) 별칭과 겹치는 이름은 별칭으로 등록할 수
        # 없다 — resolve()가 별칭을 전역 별칭보다 먼저 보므로, 등록을 허락하면 그 뒤로
        # 전역 별칭으로 찾던 곡 대신 전혀 다른 곡을 돌려주게 된다.
        conflict_title = self._find_global_name_conflict(alias, song_title)
        if conflict_title is not None:
            raise ValueError(f"'{alias}'는 이미 '{conflict_title}'에 쓰인 이름이다")

        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO songdb_aliases (game, alias, song_title, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (self._profile.game, alias, song_title, added_by, now),
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

    async def get_alias(self, *, alias: str) -> AliasRow | None:
        assert self._conn is not None
        alias = alias.lower()
        cursor = await self._conn.execute(
            "SELECT alias, song_title, added_by FROM songdb_aliases WHERE game = ? AND alias = ?",
            (self._profile.game, alias),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return AliasRow(alias=row["alias"], song_title=row["song_title"], added_by=row["added_by"])

    async def remove_alias(self, *, alias: str) -> None:
        assert self._conn is not None
        alias = alias.lower()
        await self._conn.execute("DELETE FROM songdb_aliases WHERE game = ? AND alias = ?", (self._profile.game, alias))
        await self._conn.commit()

    def song_by_title(self, title: str) -> SongRecord | None:
        """정식 제목으로 곡을 찾는다. 국제판 수록 여부는 가리지 않는다."""
        return self._songs.get(title)

    def const_for(self, title: str, difficulty: str, chart_type: str = "") -> float | None:
        chart = self.chart_for(title, difficulty, chart_type)
        return chart.const if chart else None

    def chart_for(self, title: str, difficulty: str, chart_type: str = "") -> ChartRecord | None:
        """곡 제목과 난이도 코드(채보 종류가 있는 게임이면 종류도)로 채보 하나를 찾는다(고를 수 있는지는 가리지 않는다)."""
        song = self._songs.get(title)
        return song.charts.get(chart_key(chart_type, difficulty)) if song is not None else None

    def jacket_for(self, title: str) -> str | None:
        """곡의 재킷 파일 이름(또는 전체 주소). 모르는 곡·재킷이 없는 곡은 None."""
        song = self._songs.get(title)
        return song.jacket if song is not None else None

    def jacket_fallback_for(self, title: str) -> str | None:
        """재킷을 CHUNITHM-NET에서 못 받았을 때 대신 쓸 Chunimaru 재킷 주소. 없으면 None."""
        song = self._songs.get(title)
        return song.jacket_fallback if song is not None else None

    def note_count_for(self, title: str, difficulty: str, chart_type: str = "") -> int | None:
        chart = self.chart_for(title, difficulty, chart_type)
        return chart.note_count if chart else None

    def is_new_for(self, title: str) -> bool:
        """그 곡이 신곡(신곡20 프레임 대상)인가. 모르는 곡은 False — Chunimaru 덧씌우기가
        한 번도 안 됐으면 시드에는 신곡 표시가 없으므로 전부 False다."""
        song = self._songs.get(title)
        return song.is_new if song is not None else False

    def find_by_const(
        self, *, min_const: float, max_const: float | None = None, difficulty: str | None = None,
        new_only: bool = False, chart_type: str | None = None,
    ) -> list[ConstRangeHit]:
        """상수 범위(양끝 포함)로, 지금 국제판에서 고를 수 있는 채보를 상수 내림차순(같으면
        제목순)으로 찾는다. difficulty는 짧은 코드("MAS" 등)다. 비우면 난이도를 가리지 않고
        그 상수 범위에 드는 채보를 전부 찾는다(2026-10-18 결정 — 상수는 채보 단위로 이미
        유일하게 정해지므로 난이도로 좁힐 필요가 없다는 실기 피드백). max_const를 비우면 위쪽 한계 없이
        "min 이상"을 찾고, new_only면 신곡(`is_new`)만 본다(2026-10-20 — "신곡 중 14.7 이상" 실기)."""
        hits: list[ConstRangeHit] = []
        for title, song in self._songs.items():
            if title not in self._available_intl or (new_only and not song.is_new):
                continue
            for chart in _select_charts(song, None if difficulty is None else [difficulty], chart_type):
                if not chart.is_available or chart.const is None:
                    continue
                if min_const <= chart.const and (max_const is None or chart.const <= max_const):
                    hits.append(ConstRangeHit(song=song, chart=chart))
        hits.sort(key=_const_descending)
        return hits

    def find_by_level(
        self, *, level: str, difficulty: str | None = None, chart_type: str | None = None
    ) -> list[ConstRangeHit]:
        """표기 레벨(문자열 그대로 비교, 예: "14+")로, 지금 국제판에서 고를 수 있는 채보를
        찾는다. find_by_const와 같은 가용성 규칙·difficulty 생략 의미(전체 난이도)·정렬을
        쓴다 — 상수를 모르는 채보는 뒤로 보낸다(표시에는 상수도 같이 쓸 수 있으니 숨기지
        않는다)."""
        hits: list[ConstRangeHit] = []
        for title, song in self._songs.items():
            if title not in self._available_intl:
                continue
            for chart in _select_charts(song, None if difficulty is None else [difficulty], chart_type):
                if not chart.is_available or chart.level != level:
                    continue
                hits.append(ConstRangeHit(song=song, chart=chart))
        hits.sort(key=_const_descending)
        return hits

    def find_by_version(
        self, *, version: str, difficulty: str | None = None, chart_type: str | None = None
    ) -> list[ConstRangeHit]:
        """수록 버전으로 채보를 찾는다. difficulty를 주면 find_by_const처럼 그 난이도 하나만
        본다. difficulty를 비우면("버전 전체" 폴더) 그 버전 곡들의 **고를 수 있는 채보를
        전부**(난이도마다 하나씩) 돌려준다 — 폴더 목표의 "그 버전 전체"는 특정 난이도 하나로
        좁힐 이유가 설계서에 없다(docs/design/skills/goals.md "폴더 정의"는 난이도를
        조합한다고만 적었지, 버전 폴더에서 난이도가 필수라고 하지 않았다)."""
        hits: list[ConstRangeHit] = []
        for title, song in self._songs.items():
            if title not in self._available_intl or song.version != version:
                continue
            for chart in _select_charts(song, None if difficulty is None else [difficulty], chart_type):
                if not chart.is_available:
                    continue
                hits.append(ConstRangeHit(song=song, chart=chart))
        hits.sort(key=_const_descending)
        return hits

    def find_songs(
        self, *,
        min_const: float | None = None,
        max_const: float | None = None,
        level: str | None = None,
        version: str | None = None,
        difficulty: str | Collection[str] | None = None,
        min_difficulty: str | None = None,
        max_difficulty: str | None = None,
        min_note_count: int | None = None,
        max_note_count: int | None = None,
        new_only: bool = False,
        chart_type: str | None = None,
    ) -> list[ConstRangeHit]:
        """상수 범위·레벨·수록 버전·난이도·노트 수 범위·신곡 여부를 한 번에 AND로 겹쳐 찾는다. difficulty는
        짧은 코드 하나거나 여러 개("MAS"와 "ULT")이고, 여러 개면 그중 어느 난이도든 맞는 채보를 찾는다.
        min_difficulty/max_difficulty는 BAS<ADV<EXP<MAS<ULT 순서의 범위("익스퍼트 이상")다. WORLD'S END는
        범위에 들지 않고, 범위 밖 코드를 주면 ValueError다. difficulty와 같이 주면 둘 다 맞아야 한다. chart_type은 채보 종류(maimai STD/DX)로 거르고, 안 주면 가리지 않는다.
        find_by_const/find_by_level/find_by_version은 각각 조건 하나(+난이도)만 받아서
        "15.0~15.2 상수 중 노트 수 1000개 이상"처럼 조건을 두 개 이상 동시에 거는 질문을
        못 받는다 — 그걸 위한 것이다. 준 조건만 보고 안 준 건 거르지 않는다. 조건을 하나도
        안 주면(전부 None/False) 국제판 전체를 돌려준다 — "조건이 있어야 한다"는 판단은 이
        메서드가 아니라 호출부(스킬)가 한다. new_only는 find_by_const와 같은 뜻이다(2026-10-20
        "신곡 중 14.7 이상" 실기 결정을 그대로 따른다).

        순서는 상수 내림차순(큰 것부터), 상수를 모르는 채보는 뒤, 같으면 제목순으로 고정이다. 다른 순서는
        표를 받은 `table_query`가 맡는다(2026-10-20 작업 표)."""
        wanted = [difficulty] if isinstance(difficulty, str) else None if difficulty is None else list(dict.fromkeys(difficulty))
        if chart_type is not None and chart_type not in self._profile.chart_types:
            raise ValueError(f"{self._profile.label}에는 채보 종류 {chart_type!r}가 없다: {self._profile.chart_types}")
        in_range = _difficulty_range(min_difficulty, max_difficulty, self._profile.range_codes)
        hits: list[ConstRangeHit] = []
        for title, song in self._songs.items():
            if title not in self._available_intl or (new_only and not song.is_new):
                continue
            if version is not None and song.version != version:
                continue
            for chart in _select_charts(song, wanted, chart_type):
                if not chart.is_available:
                    continue
                if in_range is not None and chart.difficulty not in in_range:
                    continue
                if min_const is not None and (chart.const is None or chart.const < min_const):
                    continue
                if max_const is not None and (chart.const is None or chart.const > max_const):
                    continue
                if level is not None and chart.level != level:
                    continue
                if min_note_count is not None and (chart.note_count is None or chart.note_count < min_note_count):
                    continue
                if max_note_count is not None and (chart.note_count is None or chart.note_count > max_note_count):
                    continue
                hits.append(ConstRangeHit(song=song, chart=chart))
        hits.sort(key=_const_descending)
        return hits
    def all_artists(self) -> list[str]:
        """지금 국제판에서 고를 수 있는 곡들의 아티스트 크레딧(중복 없이). 이름이 안 맞을 때 비슷한 후보를 찾는 데 쓴다."""
        return sorted({song.artist for title, song in self._songs.items() if title in self._available_intl and song.artist})

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
