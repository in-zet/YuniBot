# services/chunithm/table_columns.py
"""츄니즘 작업 표의 공통 칸. 칸 이름·가리키는 것 종류·보일 글자 규칙을 한곳에 둔다.

표를 만드는 모든 스킬이 이 모듈의 상수와 도우미를 쓴다 — 스킬이 칸 이름을 문자열로 직접 쓰지 않는다.
원래 값 규약(`table_query`의 `where` 해석과 맞춘다): 상수·노트 수·곡 레이팅은 숫자, 점수는 정수,
랭크는 그 랭크의 기준 점수, 램프·난이도는 정식 이름, 나머지는 글이다. 값이 없으면 원래 값 None."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal

from core.tables import (
    EMPTY_CELL,
    Cell,
    Column,
    PartialRowHint,
    RowRef,
    TableBuilder,
    TableError,
    cell,
)
from services.chunithm.records import PbRow
from services.chunithm.songdb import (
    LONG_TO_SHORT_DIFFICULTY,
    VERSION_ORDER,
    ChartRecord,
    SongRecord,
)

SONG = "곡"
DIFFICULTY = "난이도"
LEVEL = "레벨"
CONST = "상수"
NOTE_COUNT = "노트 수"
VERSION = "버전"
ARTIST = "작곡가"
SCORE = "점수"
RANK = "랭크"
LAMP = "램프"
RATING = "곡 레이팅"
SONG_RAW_NAME = "곡 이름(원문)"

# 가리키는 것 종류. 키는 `chart_ref`·`song_ref`가 만든다.
CHART = "chunithm.chart"
SONG_KIND = "chunithm.song"

NO_RECORD = "기록 없음"
_DASH = "-"
_UNKNOWN = "계산 불가"
_NO_LAMP = "NONE"

# 난이도 칸의 원래 값(정식 이름)을 쉬운 쪽부터 나열한 순서. 정렬·묶기·크기 비교(`난이도 >= MASTER`)가 이 순서를 따른다.
DIFFICULTY_ORDER: tuple[str, ...] = tuple(LONG_TO_SHORT_DIFFICULTY)

SHORT_TO_LONG_DIFFICULTY: dict[str, str] = {short: long for long, short in LONG_TO_SHORT_DIFFICULTY.items()}

# 곡 단위 행(곡은 차 있고 난이도만 빈 행)을 난이도가 필요한 도구가 뺄 때 붙이는 안내.
PARTIAL_ROW_HINTS: tuple[PartialRowHint, ...] = (
    PartialRowHint(
        column=DIFFICULTY,
        given=SONG,
        label="곡 단위 행(난이도 없음)",
        advice="charts_of로 난이도를 정할 수 있다",
    ),
)

_COLUMNS: dict[str, Column] = {
    SONG: Column(name=SONG, label=SONG, bare=True),
    DIFFICULTY: Column(name=DIFFICULTY, label=DIFFICULTY, value_type="DifficultyRef", bare=True, order=DIFFICULTY_ORDER),
    LEVEL: Column(name=LEVEL, label=LEVEL),
    CONST: Column(name=CONST, label=CONST, value_type="number"),
    NOTE_COUNT: Column(name=NOTE_COUNT, label=NOTE_COUNT, value_type="number"),
    VERSION: Column(name=VERSION, label=VERSION, order=VERSION_ORDER),
    ARTIST: Column(name=ARTIST, label=ARTIST),
    SCORE: Column(name=SCORE, label=SCORE, value_type="ScoreRef"),
    RANK: Column(name=RANK, label=RANK, value_type="RankRef"),
    LAMP: Column(name=LAMP, label=LAMP, value_type="LampRef"),
    RATING: Column(name=RATING, label=RATING, value_type="number"),
    SONG_RAW_NAME: Column(name=SONG_RAW_NAME, label=SONG_RAW_NAME),
}


def common_columns(*names: str, hidden_in_text: Collection[str] = ()) -> list[Column]:
    """공통 칸 정의를 주어진 순서대로 꺼낸다. `hidden_in_text`에 든 칸은 글자 출력에서 숨긴다."""
    columns: list[Column] = []
    for name in names:
        column = _COLUMNS.get(name)
        if column is None:
            raise TableError(f"공통 칸이 아니다: {name}")
        columns.append(replace(column, hidden_in_text=name in hidden_in_text))
    return columns


def add_columns(
    builder: TableBuilder, *names: str, hidden_in_text: Collection[str] = ()
) -> TableBuilder:
    """공통 칸을 주어진 순서대로 표 만들기에 더한다."""
    for column in common_columns(*names, hidden_in_text=hidden_in_text):
        builder.column(
            column.name, column.label, column.value_type, slash_only=column.slash_only,
            hidden_in_text=column.hidden_in_text, bare=column.bare, order=column.order,
        )
    return builder


# ---------------------------------------------------------------- 가리키는 것


def chart_ref(title: str, difficulty: str) -> RowRef:
    """채보 행의 가리키는 것. 키는 (정식 제목, 난이도 코드)."""
    return RowRef(CHART, json.dumps([title, difficulty], ensure_ascii=False))


def song_ref(title: str) -> RowRef:
    return RowRef(SONG_KIND, title)


def chart_key(ref: RowRef) -> tuple[str, str]:
    """`chart_ref`의 키를 (정식 제목, 난이도 코드)로 되돌린다."""
    if ref.kind != CHART:
        raise TableError("채보 행이 아니다")
    title, difficulty = json.loads(ref.key)
    return str(title), str(difficulty)


# ---------------------------------------------------------------- 칸 하나의 값


def song_cell(title: str | None) -> Cell:
    return EMPTY_CELL if not title else cell(title)


def difficulty_cell(code: str | None) -> Cell:
    """원래 값은 정식 이름("MASTER"), 보일 글자는 짧은 코드("MAS")."""
    if not code:
        return EMPTY_CELL
    return cell(SHORT_TO_LONG_DIFFICULTY.get(code, code), code)


def level_cell(level: str | None) -> Cell:
    return cell(level) if level else cell(None, _DASH)


def const_cell(const: float | None, *, estimated: bool = False) -> Cell:
    if const is None:
        return cell(None, _DASH)
    return cell(const, f"{const:.1f}{'(추정)' if estimated else ''}")


def note_count_cell(note_count: int | None) -> Cell:
    return cell(note_count) if note_count is not None else cell(None, _DASH)


def text_cell(value: str | None) -> Cell:
    return cell(value) if value else cell(None, _DASH)


def score_cell(score: int | None) -> Cell:
    return cell(score, f"{score:,}") if score is not None else cell(None, NO_RECORD)


def rank_cell(rank: str | None, rank_scores: Mapping[str, int]) -> Cell:
    """원래 값은 그 랭크의 기준 점수. 기준 점수를 모르는 랭크면 원래 값 None."""
    if not rank:
        return cell(None, _DASH)
    return cell(rank_scores.get(rank), rank)


def lamp_cell(combo_lamp: str | None) -> Cell:
    """기록이 없으면 원래 값 None. 기록은 있는데 램프가 없으면 원래 값 "NONE"(조건에 걸리지 않는다)."""
    if combo_lamp is None:
        return cell(None, _DASH)
    if combo_lamp in ("", _NO_LAMP):
        return cell(_NO_LAMP, _DASH)
    return cell(combo_lamp)


def rating_cell(rating: Decimal | None, *, has_record: bool = True) -> Cell:
    """곡 레이팅은 소수 둘째 자리 값을 그대로 보인다(records가 이미 버림한 값)."""
    if rating is None:
        return cell(None, _UNKNOWN if has_record else _DASH)
    return cell(float(rating), str(rating))


def _num_text(value: float) -> str:
    return f"{value:.1f}" if round(value, 1) == value else f"{value:g}"


def const_range_text(min_const: float | None, max_const: float | None) -> str | None:
    """steps에 쓰는 상수 범위 문구("15.0~15.5 상수", "15.0 이상 상수"). 둘 다 없으면 None."""
    if min_const is None and max_const is None:
        return None
    if max_const is None and min_const is not None:
        return f"{_num_text(min_const)} 이상 상수"
    if min_const is None and max_const is not None:
        return f"{_num_text(max_const)} 이하 상수"
    assert min_const is not None and max_const is not None
    return f"{_num_text(min_const)}~{_num_text(max_const)} 상수"


# ---------------------------------------------------------------- 행 하나의 칸 묶음


def chart_cells(song: SongRecord, chart: ChartRecord, names: Sequence[str]) -> dict[str, Cell]:
    """곡과 채보 하나에서 `names`에 든 공통 칸의 셀을 만든다(채보에서 알 수 있는 칸만)."""
    available: dict[str, Cell] = {
        SONG: song_cell(song.title),
        DIFFICULTY: difficulty_cell(chart.difficulty),
        LEVEL: level_cell(chart.level),
        CONST: const_cell(chart.const, estimated=chart.is_const_unknown),
        NOTE_COUNT: note_count_cell(chart.note_count),
        VERSION: text_cell(song.version),
        ARTIST: text_cell(song.artist),
    }
    return {name: available[name] for name in names if name in available}


def song_cells(song: SongRecord, names: Sequence[str]) -> dict[str, Cell]:
    """곡 단위 행의 셀. 난이도는 비운다. 채보에서만 아는 칸(레벨·상수·노트 수)은 만들지 않는다."""
    available: dict[str, Cell] = {
        SONG: song_cell(song.title),
        DIFFICULTY: EMPTY_CELL,
        VERSION: text_cell(song.version),
        ARTIST: text_cell(song.artist),
    }
    return {name: available[name] for name in names if name in available}


def pb_cells(pb: PbRow | None, rank_scores: Mapping[str, int]) -> dict[str, Cell]:
    """PB(없으면 None)에서 점수·랭크·램프·곡 레이팅 셀을 만든다. PB가 없으면 원래 값이 모두 None이다."""
    if pb is None:
        return {
            SCORE: score_cell(None),
            RANK: rank_cell(None, rank_scores),
            LAMP: lamp_cell(None),
            RATING: rating_cell(None, has_record=False),
        }
    return {
        SCORE: score_cell(pb.score),
        RANK: rank_cell(pb.rank, rank_scores),
        LAMP: lamp_cell(pb.combo_lamp),
        RATING: rating_cell(pb.rating),
    }
