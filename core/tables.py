# core/tables.py
from __future__ import annotations

import re
import time
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Literal, Self, TypedDict

ValueType = Literal["text", "number", "ScoreRef", "RankRef", "LampRef", "DifficultyRef"]
VALUE_TYPES: frozenset[str] = frozenset(
    {"text", "number", "ScoreRef", "RankRef", "LampRef", "DifficultyRef"}
)


class TableError(ValueError):
    """작업 표를 만들거나 읽을 때의 오류. 메시지는 그대로 모델/사용자에게 보여도 되는 말이다."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Column:
    """표의 칸 정의. `name`은 칸을 가리키는 안정된 이름(공통 칸이면 그 상수), `label`은 사람과 모델이 보는 이름이다."""

    name: str
    label: str
    value_type: ValueType = "text"
    # 슬래시 출력에서만 보이는 칸(다음 슬래시 명령에 타이핑할 id 등).
    slash_only: bool = False
    # 글자 출력에서는 숨기지만 거르기·정렬·모델 요약에는 그대로 쓰는 칸.
    hidden_in_text: bool = False
    # 기본 출력에서 칸 이름 없이 값만 쓰는 칸(곡·난이도).
    bare: bool = False


@dataclass(frozen=True, slots=True)
class Cell:
    """칸의 값 하나. `raw`는 거르기·정렬용 원래 값(없으면 None), `text`는 출력용 글자다."""

    raw: Any | None = None
    text: str = ""

    @property
    def filled(self) -> bool:
        return self.raw is not None and self.text != ""


def cell(raw: Any | None, text: str | None = None) -> Cell:
    """`text`를 안 주면 원래 값을 글자로 바꾼다. 값이 없으면 빈 글자."""
    if text is None:
        text = "" if raw is None else str(raw)
    return Cell(raw, text)


EMPTY_CELL = Cell()


@dataclass(frozen=True, slots=True)
class RowRef:
    """행이 가리키는 것: 종류와 키. 키는 보관만 하고 모델에게 보이지 않는다."""

    kind: str
    key: str


@dataclass(frozen=True, slots=True)
class Row:
    ref: RowRef
    cells: Mapping[str, Cell]

    def cell(self, name: str) -> Cell:
        return self.cells.get(name, EMPTY_CELL)


@dataclass(frozen=True, slots=True)
class Group:
    """묶음. `title`이 None이면 이름 없는 묶음이다."""

    title: str | None
    rows: tuple[Row, ...]
    summary: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkTable:
    """코어가 보관하는 작업 표. 한 번 만들면 바꾸지 않는다 — 거르거나 칸을 더하면 새 표를 만든다."""

    columns: tuple[Column, ...]
    groups: tuple[Group, ...]
    id: str = ""
    summary: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    steps: tuple[str, ...] = ()
    # 표의 데이터가 누구 것인지(플레이어 id). 플레이어와 무관한 표는 None.
    subject: str | None = None
    # 표지의 개수 단위("채보", "개").
    unit: str = "개"
    # 한 줄 서식. 자리는 `{칸 보이는 이름}`.
    row_formats: tuple[str, ...] = ()

    @property
    def rows(self) -> tuple[Row, ...]:
        return tuple(row for group in self.groups for row in group.rows)

    @property
    def row_count(self) -> int:
        return sum(len(group.rows) for group in self.groups)

    def numbered_rows(self) -> Iterator[tuple[int, Row]]:
        """묶음 순서대로 늘어놓은 행에 1부터 번호를 붙인다. 출력에 찍는 번호와 같다."""
        number = 0
        for group in self.groups:
            for row in group.rows:
                number += 1
                yield number, row

    def column_by_label(self, label: str) -> Column | None:
        for column in self.columns:
            if column.label == label:
                return column
        return None

    def column_by_name(self, name: str) -> Column | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None


def row_at(table: WorkTable, number: int) -> Row:
    """표에 찍힌 번호(1부터)의 행. 출력·텍스트 파일·그림 표·고르기가 모두 이 번호를 쓴다."""
    count = table.row_count
    if number < 1 or number > count:
        raise TableError(f"그 표는 {count}행까지 있다")
    for n, row in table.numbered_rows():
        if n == number:
            return row
    raise TableError(f"그 표는 {count}행까지 있다")  # pragma: no cover


def derive_table(table: WorkTable, groups: Iterable[Group], *, steps: Iterable[str] = ()) -> WorkTable:
    """같은 칸 정의로 행만 바꾼 새 표. id는 비우고, 원래 행 전체에서만 맞는 표·묶음 요약은 버린다.
    비어 버린 묶음은 뺀다. 표지 이름은 남는다."""
    kept = tuple(
        Group(group.title, group.rows) for group in groups if group.rows
    )
    return replace(table, id="", groups=kept, summary=MappingProxyType({}), steps=(*table.steps, *steps))


def _new_columns(table: WorkTable, columns: Sequence[Column]) -> list[Column]:
    """`columns` 중 표에 아직 없는 칸. 이름이 같으면 이미 있는 것으로 보고, 보이는 이름만 겹치면 에러."""
    known = {column.name for column in table.columns}
    labels = {column.label for column in table.columns}
    added: list[Column] = []
    for column in columns:
        if column.name in known:
            continue
        if column.label in labels:
            raise TableError(f"칸 보이는 이름이 겹친다: {column.label}")
        added.append(column)
    return added


def expand_rows(
    table: WorkTable,
    expand: Callable[[Row], Sequence[Row] | None],
    columns: Sequence[Column] = (),
    *,
    steps: Iterable[str] = (),
) -> WorkTable:
    """행을 바꾼 새 표(곡 행을 채보 행으로 펼치는 도구의 바탕). `expand(행)`이 None이면 그 행은 그대로,
    빈 목록이면 뺀다. 아니면 돌려준 행들이 그 자리에 들어간다. 칸을 더하는 규칙은 `extend_table`과 같다."""
    added = _new_columns(table, columns)
    groups: list[Group] = []
    for group in table.groups:
        rows: list[Row] = []
        for row in group.rows:
            replaced = expand(row)
            rows.extend([row] if replaced is None else replaced)
        groups.append(Group(group.title, tuple(rows)))
    derived = derive_table(table, groups, steps=steps)
    return replace(derived, columns=(*table.columns, *added))


def extend_table(
    table: WorkTable,
    columns: Sequence[Column],
    cells_for: Callable[[Row], Mapping[str, Cell]],
    *,
    steps: Iterable[str] = (),
) -> WorkTable:
    """칸을 더한 새 표(칸 더하기 도구의 바탕). 행은 그대로이고 `cells_for(행)`이 돌려준 셀이 그 행에 더해진다.
    이미 있는 칸(이름이 같음)이면 정의는 그대로 두고 셀만 덮어쓴다. 보이는 이름만 겹치면 에러.
    `derive_table`처럼 id와 요약은 비우고 표지 이름은 남긴다."""
    added = _new_columns(table, columns)
    groups = [
        Group(
            group.title,
            tuple(Row(row.ref, MappingProxyType({**row.cells, **cells_for(row)})) for row in group.rows),
        )
        for group in table.groups
    ]
    derived = derive_table(table, groups, steps=steps)
    return replace(derived, columns=(*table.columns, *added))


def keep_rows(table: WorkTable, keep: Callable[[int, Row], bool], *, steps: Iterable[str] = ()) -> WorkTable:
    """`keep(번호, 행)`이 참인 행만 남긴 새 표. 번호는 원래 표의 번호다."""
    number = 0
    groups: list[Group] = []
    for group in table.groups:
        rows: list[Row] = []
        for row in group.rows:
            number += 1
            if keep(number, row):
                rows.append(row)
        groups.append(Group(group.title, tuple(rows)))
    return derive_table(table, groups, steps=steps)


def pick_numbers(table: WorkTable, numbers: Iterable[int]) -> WorkTable:
    """표에 찍힌 번호의 행만 남긴 새 표. 없는 번호가 하나라도 있으면 에러. 행 순서는 원래 표를 따른다."""
    wanted = set(numbers)
    count = table.row_count
    if any(n < 1 or n > count for n in wanted):
        raise TableError(f"그 표는 {count}행까지 있다")
    return keep_rows(table, lambda n, _row: n in wanted, steps=[_numbers_step(wanted)])


def _numbers_step(numbers: Collection[int]) -> str:
    return "·".join(str(n) for n in sorted(numbers)) + "번"


COMPARE_OPS: frozenset[str] = frozenset({"==", "!=", "<", "<=", ">", ">=", "contains"})
# 크기 비교(<, <=, >, >=)를 할 수 없는 칸 종류. 이름이 같은지만 본다.
_UNORDERED_TYPES: frozenset[str] = frozenset({"LampRef", "DifficultyRef"})
# 한 번에 돌려주는 행 수의 상한(`table_peek`).
PEEK_MAX_ROWS = 20


@dataclass(frozen=True, slots=True)
class Condition:
    """`where` 항목 하나. `value`는 칸의 값 종류로 이미 해석한 비교용 값이고, `text`는 모델이 넘긴 원문(steps용)이다."""

    column: str
    op: str
    value: Any
    text: str


class WhereClause(TypedDict):
    """모델이 넘기는 조건. `column`은 칸 보이는 이름, `value`는 칸 값 종류로 해석할 원문이다."""

    column: str
    op: str
    value: str


_NumberedGroup = tuple[str | None, list[tuple[int, Row]]]


def _numbered_groups(table: WorkTable) -> list[_NumberedGroup]:
    groups: list[_NumberedGroup] = []
    number = 0
    for group in table.groups:
        rows: list[tuple[int, Row]] = []
        for row in group.rows:
            number += 1
            rows.append((number, row))
        groups.append((group.title, rows))
    return groups


def _column_by_label(table: WorkTable, label: str) -> Column:
    column = table.column_by_label(label)
    if column is None:
        raise TableError(f"그 표에 '{label}' 칸이 없다")
    return column


def _check_numbers(table: WorkTable, numbers: Iterable[int]) -> set[int]:
    wanted = set(numbers)
    count = table.row_count
    if any(n < 1 or n > count for n in wanted):
        raise TableError(f"그 표는 {count}행까지 있다")
    return wanted


def _passes(raw: Any, op: str, value: Any, column: Column) -> bool:
    if raw is None:
        return True  # 값이 없는 행은 어떤 조건이든 통과한다(임시 결정)
    try:
        if op == "contains":
            if not isinstance(raw, str):
                raise TypeError
            return str(value).casefold() in raw.casefold()
        if op == "==":
            return bool(raw == value)
        if op == "!=":
            return bool(raw != value)
        if column.value_type in _UNORDERED_TYPES:
            raise TableError(f"'{column.label}' 칸은 크기를 비교할 수 없다")
        if op == "<":
            return bool(raw < value)
        if op == "<=":
            return bool(raw <= value)
        if op == ">":
            return bool(raw > value)
        return bool(raw >= value)
    except TypeError:
        raise TableError(f"'{column.label}' 칸은 '{value}'와 비교할 수 없다") from None


def _condition_step(condition: Condition) -> str:
    if condition.op == "contains":
        return f"{condition.column}에 {condition.text} 포함"
    return f"{condition.column}{condition.op}{condition.text}"


def _sorted_rows(
    rows: list[tuple[int, Row]], column: Column, *, descending: bool
) -> list[tuple[int, Row]]:
    present = [item for item in rows if item[1].cell(column.name).raw is not None]
    absent = [item for item in rows if item[1].cell(column.name).raw is None]
    def raw_of(item: tuple[int, Row]) -> Any:
        return item[1].cell(column.name).raw

    try:
        present.sort(key=raw_of, reverse=descending)
    except TypeError:
        raise TableError(f"'{column.label}' 칸은 정렬할 수 없다") from None
    return [*present, *absent]  # 같은 값이면 원래 순서를 지킨다(안정 정렬). 값이 없는 행은 방향과 무관하게 맨 뒤


def _regroup(groups: list[_NumberedGroup], column: Column) -> list[_NumberedGroup]:
    buckets: dict[Any, list[tuple[int, Row]]] = {}
    titles: dict[Any, str] = {}
    for _title, rows in groups:
        for item in rows:
            cell_ = item[1].cell(column.name)
            buckets.setdefault(cell_.raw, []).append(item)
            titles.setdefault(cell_.raw, cell_.text or "(없음)")
    keys = [key for key in buckets if key is not None]
    try:
        keys.sort(reverse=True)
    except TypeError:
        raise TableError(f"'{column.label}' 칸은 묶을 수 없다") from None
    if None in buckets:
        keys.append(None)
    return [(titles[key], buckets[key]) for key in keys]


def _select_numbered(
    table: WorkTable,
    *,
    rows: Sequence[int] | None = None,
    exclude_rows: Sequence[int] | None = None,
    where: Sequence[Condition] = (),
    sort: str | None = None,
    descending: bool = False,
    limit: int | None = None,
    group_by: str | None = None,
) -> tuple[list[_NumberedGroup], list[str]]:
    """거르기·정렬 엔진. 순서는 번호 고르기 → where → 다시 묶기 → 정렬 → 묶음마다 limit이다."""
    steps: list[str] = []
    groups = _numbered_groups(table)
    if rows is not None:
        wanted = _check_numbers(table, rows)
        groups = [(t, [item for item in rs if item[0] in wanted]) for t, rs in groups]
        steps.append(_numbers_step(wanted))
    if exclude_rows is not None:
        dropped = _check_numbers(table, exclude_rows)
        groups = [(t, [item for item in rs if item[0] not in dropped]) for t, rs in groups]
        steps.append(_numbers_step(dropped) + " 뺌")
    for condition in where:
        if condition.op not in COMPARE_OPS:
            raise TableError(f"모르는 비교 '{condition.op}' (쓸 수 있는 것: {', '.join(sorted(COMPARE_OPS))})")
        column = _column_by_label(table, condition.column)
        groups = [
            (t, [item for item in rs if _passes(item[1].cell(column.name).raw, condition.op, condition.value, column)])
            for t, rs in groups
        ]
        steps.append(_condition_step(condition))
    if group_by is not None:
        if group_by == "":
            groups = [(None, [item for _t, rs in groups for item in rs])]
            steps.append("묶음 풀기")
        else:
            groups = _regroup(groups, _column_by_label(table, group_by))
            steps.append(f"{group_by}별 묶음")
    if sort is not None:
        column = _column_by_label(table, sort)
        groups = [(t, _sorted_rows(rs, column, descending=descending)) for t, rs in groups]
        steps.append(f"{sort} {'내림' if descending else '오름'}차순")
    if limit is not None:
        if limit < 1:
            raise TableError("limit은 1 이상이어야 한다")
        groups = [(t, rs[:limit]) for t, rs in groups]
        steps.append(f"앞 {limit}개" if len([1 for _t, rs in groups if rs]) <= 1 else f"묶음마다 앞 {limit}개")
    return [(t, rs) for t, rs in groups if rs], steps


def select_rows(table: WorkTable, **options: Any) -> WorkTable:
    """`table_query`의 엔진. 조건에 맞는 행으로 새 표를 만든다(원래 표는 그대로). 표·묶음 요약은 버리고
    `steps`에 조건을 덧붙인다. 옵션은 `_select_numbered`와 같다."""
    groups, steps = _select_numbered(table, **options)
    return derive_table(table, [Group(title, tuple(row for _n, row in rs)) for title, rs in groups], steps=steps)


def peek_rows(table: WorkTable, *, limit: int = 10, **options: Any) -> dict[str, Any]:
    """`table_peek`의 엔진. 고른 행의 원래 값을 돌려준다. 행마다 원래 표의 번호를 붙인다."""
    if options.get("group_by") is not None or options.get("exclude_rows") is not None:
        raise TableError("table_peek은 묶기·번호 빼기를 쓰지 않는다")
    options = dict(options)
    limit = max(1, min(limit, PEEK_MAX_ROWS))
    if options.get("sort") is not None:
        options["group_by"] = ""  # 훑어보기는 묶음과 무관하게 전체에서 정렬한다
    groups, _steps = _select_numbered(table, **options)
    flat = [item for _t, rs in groups for item in rs]
    visible = [column for column in table.columns if not column.slash_only]
    return {
        "table": table.id,
        "matched": len(flat),
        "rows": [
            {"number": number, "values": {column.label: row.cell(column.name).raw for column in visible}}
            for number, row in flat[:limit]
        ],
    }


@dataclass(frozen=True, slots=True, init=False)
class TableNeeds:
    """표를 받는 도구가 선언하는 필요한 것. 쓰는 것만 준다.

    `ref_kind`: 행이 가리키는 것의 종류. `columns`: 표에 있어야 하는 칸 이름. `nonempty`: 행에서 차 있어야 하는
    칸 이름. `single`: 남은 행이 정확히 하나여야 한다(항목 하나만 다루는 도구)."""

    ref_kind: str | None
    columns: tuple[str, ...]
    nonempty: tuple[str, ...]
    single: bool

    def __init__(
        self,
        *,
        ref_kind: str | None = None,
        columns: Sequence[str] = (),
        nonempty: Sequence[str] = (),
        single: bool = False,
    ) -> None:
        object.__setattr__(self, "ref_kind", ref_kind)
        object.__setattr__(self, "columns", tuple(columns))
        object.__setattr__(self, "nonempty", tuple(nonempty))
        object.__setattr__(self, "single", single)


@dataclass(frozen=True, slots=True, kw_only=True)
class PartialRowHint:
    """`column`이 비었지만 `given`은 차 있는 행("곡 단위 행")을 뺄 때 붙이는 따로 센 문구.
    코어는 칸의 뜻을 모르므로 칸 이름과 문구를 공통 칸 모듈이 정해서 넘겨 준다."""

    column: str
    given: str
    label: str
    advice: str


_TABLE_ARG_RE = re.compile(r"t(\d+)(?::(\d+(?:,\d+)*))?")


def parse_table_arg(raw: str) -> tuple[str, list[int] | None]:
    """`"t3"` 또는 `"t3:2,3"`을 (표 id, 번호 목록 또는 None)으로 나눈다."""
    compact = "".join(raw.split())
    match = _TABLE_ARG_RE.fullmatch(compact)
    if match is None:
        raise TableError("표 id 모양이 틀렸다")
    numbers = None if match.group(2) is None else [int(n) for n in match.group(2).split(",")]
    return f"t{int(match.group(1))}", numbers


@dataclass(frozen=True, slots=True)
class TableArg:
    table: WorkTable
    # 입력 표가 옛 값이다(결과 표도 옛 값이 된다).
    stale: bool


def resolve_table_arg(
    raw: object,
    *,
    store: TableStore | None,
    player_id: str,
    channel_id: str | None,
    needs: TableNeeds | None,
    mutates: bool,
    turn_table_ids: Collection[str] = (),
    partial_row_hints: Sequence[PartialRowHint] = (),
) -> TableArg:
    """`TableRef` 인자를 도구에 넘길 표로 바꾼다. 실패하면 `TableError`(메시지는 그대로 보여도 된다)."""
    if not isinstance(raw, str):
        raise TableError("표 id 모양이 틀렸다")
    table_id, numbers = parse_table_arg(raw)
    table = None if store is None or channel_id is None else store.get(player_id, channel_id, table_id)
    if table is None or store is None or channel_id is None:
        raise TableError("그 표 없음(지난 표이거나 다른 채널의 표)")
    stale = store.is_stale(player_id, channel_id, table_id)
    if mutates and stale:
        raise TableError("표가 동기화 전 값이라 다시 만들어야 한다")
    if numbers is not None:
        if table_id in turn_table_ids:
            raise TableError("번호는 사람이 본 표에만 쓴다")
        table = pick_numbers(table, numbers)
    if needs is not None:
        table = _apply_needs(table, needs, partial_row_hints)
    return TableArg(table, stale)


def _apply_needs(table: WorkTable, needs: TableNeeds, hints: Sequence[PartialRowHint]) -> WorkTable:
    for name in needs.columns:
        if table.column_by_name(name) is None:
            raise TableError(f"그 표에는 '{name}' 칸이 없다")

    def label(name: str) -> str:
        column = table.column_by_name(name)
        return name if column is None else column.label

    hint_by_column = {hint.column: hint for hint in hints}
    dropped_kind = 0
    dropped_by_column: dict[str, int] = {}
    dropped_partial: dict[str, int] = {}

    def keep(_number: int, row: Row) -> bool:
        nonlocal dropped_kind
        if needs.ref_kind is not None and row.ref.kind != needs.ref_kind:
            dropped_kind += 1
            return False
        for name in needs.nonempty:
            if row.cell(name).filled:
                continue
            hint = hint_by_column.get(name)
            if hint is not None and row.cell(hint.given).filled:
                dropped_partial[name] = dropped_partial.get(name, 0) + 1
            else:
                dropped_by_column[name] = dropped_by_column.get(name, 0) + 1
            return False
        return True

    reduced = keep_rows(table, keep)
    reasons: list[str] = []
    if dropped_kind:
        reasons.append(f"대상 종류가 아닌 행 {dropped_kind}개 뺌")
    for name in needs.nonempty:
        if name in dropped_by_column:
            reasons.append(f"{label(name)} 없는 행 {dropped_by_column[name]}개 뺌")
        if name in dropped_partial:
            hint = hint_by_column[name]
            reasons.append(f"{hint.label} {dropped_partial[name]}개 뺌 — {hint.advice}")
    removed = table.row_count - reduced.row_count
    if reduced.row_count == 0:
        raise TableError(f"대상 행이 없다(전부 {removed}개 뺌: {'; '.join(reasons)})" if removed else "대상 행이 없다")
    if needs.single and reduced.row_count != 1:
        raise TableError(f"한 행만 골라야 한다(지금 {reduced.row_count}개)")
    if not removed:
        return table
    return replace(reduced, steps=(*table.steps, *reasons))


def summarize_table(table: WorkTable, *, stale: bool = False) -> dict[str, Any]:
    """모델이 표에 대해 받는 전부. 행 값·가리키는 것의 키·`subject`·`slash_only` 칸은 넣지 않는다."""
    summary: dict[str, Any] = {
        "table": table.id,
        "rows": table.row_count,
        "unit": table.unit,
        "columns": [column.label for column in table.columns if not column.slash_only],
    }
    if any(group.title is not None for group in table.groups):
        groups: list[dict[str, Any]] = []
        for group in table.groups:
            entry: dict[str, Any] = {"title": group.title, "rows": len(group.rows)}
            if group.summary:
                entry["summary"] = dict(group.summary)
            groups.append(entry)
        summary["groups"] = groups
    if table.steps:
        summary["steps"] = list(table.steps)
    if table.summary:
        summary["summary"] = dict(table.summary)
    if stale:
        summary["stale"] = True
    return summary


# 행이 이 수를 넘으면(11행부터) 행 목록을 메시지에 펼치지 않고 .txt 파일로 첨부한다.
TABLE_FILE_ROWS = 10
STALE_NOTE = "(동기화 전 값)"
_FORMAT_SLOT_RE = re.compile(r"\{([^{}]+)\}")
_FILENAME_KEEP_RE = re.compile(r"[^\w.\-]+")


def _visible_columns(table: WorkTable, *, slash: bool) -> list[Column]:
    return [
        column
        for column in table.columns
        if not column.hidden_in_text and (slash or not column.slash_only)
    ]


def row_text(table: WorkTable, row: Row, *, slash: bool = False) -> str:
    """행 한 줄의 글. 보일 칸만 골라, 칸이 모두 찬 첫 한 줄 서식을 쓰고, 없으면 기본 출력(칸 이름 + 값)으로 쓴다."""
    visible = _visible_columns(table, slash=slash)
    by_label = {column.label: column for column in visible}
    used: set[str] = set()
    line: str | None = None
    for fmt in table.row_formats:
        labels = _FORMAT_SLOT_RE.findall(fmt)
        if all(label in by_label and row.cell(by_label[label].name).filled for label in labels):
            line = _FORMAT_SLOT_RE.sub(lambda m: row.cell(by_label[m.group(1)].name).text, fmt)
            used = set(labels)
            break
    parts: list[str] = [] if line is None else [line]
    for column in visible:
        cell_ = row.cell(column.name)
        if column.label in used or not cell_.filled:
            continue
        parts.append(cell_.text if column.bare and line is None else f"{column.label} {cell_.text}")
    return "  ".join(parts)


@dataclass(frozen=True, slots=True)
class RenderedTable:
    """글자로 그린 표. `file`이 있으면 행 목록은 거기 있고 `content`에는 머리말만 있다."""

    content: str
    file: tuple[str, bytes] | None = None


def _header_lines(table: WorkTable, *, stale: bool) -> list[str]:
    lines: list[str] = []
    if stale:
        lines.append(STALE_NOTE)
    if table.steps:
        lines.append(" → ".join(table.steps))
    if table.summary:
        lines.append(" · ".join(f"{key} {value}" for key, value in table.summary.items()))
    return lines


def _body_lines(table: WorkTable, *, slash: bool) -> list[str]:
    lines: list[str] = []
    number = 0
    bare_single = len(table.groups) == 1 and table.groups[0].title is None
    for group in table.groups:
        if not bare_single:
            rule = [] if group.title is None else [group.title]
            rule.append(f"{len(group.rows)}{table.unit}")
            rule.extend(f"{key} {value}" for key, value in group.summary.items())
            lines.append(f"── {' · '.join(rule)} ──")
        for row in group.rows:
            number += 1
            lines.append(f"{number}. {row_text(table, row, slash=slash)}")
    return lines


def table_file_name(table: WorkTable) -> str:
    """파일 이름은 `steps` 첫 항목에서 따온다(예 `15.0상수MAS.txt`). 쓸 글자가 없으면 `table.txt`."""
    stem = _FILENAME_KEEP_RE.sub("", table.steps[0])[:40] if table.steps else ""
    return f"{stem or 'table'}.txt"


def render_text(table: WorkTable, *, stale: bool = False, slash: bool = False) -> RenderedTable:
    """표를 글자로 그린다. 머리말(옛 값 표시·steps·요약) 뒤에 묶음마다 표지 줄과 `번호. 행 글`이 온다.
    행이 `TABLE_FILE_ROWS`를 넘으면 머리말만 `content`에 두고 전체를 .txt 파일로 돌려준다."""
    header = _header_lines(table, stale=stale)
    body = _body_lines(table, slash=slash)
    if table.row_count <= TABLE_FILE_ROWS:
        return RenderedTable("\n".join([*header, *body]))
    name = table_file_name(table)
    note = f"{table.row_count}{table.unit} — 목록은 첨부 파일({name})"
    return RenderedTable("\n".join([*header, note]), (name, "\n".join([*header, *body]).encode("utf-8")))


def describe_table(table: WorkTable, *, stale: bool = False) -> str:
    """살아 있는 표를 프롬프트에 한 줄로 알린다. 행 값은 넣지 않는다."""
    flag = f" {STALE_NOTE}" if stale else ""
    process = " → ".join(table.steps) if table.steps else "과정 없음"
    labels = "·".join(column.label for column in table.columns if not column.slash_only)
    return f"[살아 있는 표] {table.id}{flag} — {process} ({table.row_count}{table.unit}, 칸: {labels})"


class TableBuilder:
    """스킬이 표를 만드는 도우미. id는 비워 두고 코어가 보관할 때 붙인다."""

    def __init__(self, *, subject: str | None = None, unit: str = "개") -> None:
        self._columns: list[Column] = []
        self._groups: list[tuple[str | None, dict[str, str], list[Row]]] = []
        self._summary: dict[str, str] = {}
        self._steps: list[str] = []
        self._row_formats: list[str] = []
        self._subject = subject
        self._unit = unit

    @classmethod
    def from_table(cls, table: WorkTable) -> Self:
        """거르거나 칸을 더한 새 표의 바탕. 칸·묶음·행·요약·steps·서식을 그대로 복사한다."""
        builder = cls(subject=table.subject, unit=table.unit)
        builder._columns = list(table.columns)
        builder._groups = [
            (g.title, dict(g.summary), list(g.rows)) for g in table.groups
        ]
        builder._summary = dict(table.summary)
        builder._steps = list(table.steps)
        builder._row_formats = list(table.row_formats)
        return builder

    def column(
        self,
        name: str,
        label: str | None = None,
        value_type: ValueType = "text",
        *,
        slash_only: bool = False,
        hidden_in_text: bool = False,
        bare: bool = False,
    ) -> Self:
        if value_type not in VALUE_TYPES:
            raise TableError(f"칸 값 종류가 틀렸다: {value_type}")
        label = name if label is None else label
        for existing in self._columns:
            if existing.name == name:
                raise TableError(f"칸 이름이 겹친다: {name}")
            if existing.label == label:
                raise TableError(f"칸 보이는 이름이 겹친다: {label}")
        self._columns.append(
            Column(
                name=name,
                label=label,
                value_type=value_type,
                slash_only=slash_only,
                hidden_in_text=hidden_in_text,
                bare=bare,
            )
        )
        return self

    def group(
        self, title: str | None = None, summary: Mapping[str, str] | None = None
    ) -> Self:
        """새 묶음을 시작한다. 이후 `row`는 이 묶음에 들어간다."""
        self._groups.append((title, dict(summary or {}), []))
        return self

    def row(self, ref: RowRef, cells: Mapping[str, Cell]) -> Self:
        names = {column.name for column in self._columns}
        unknown = [name for name in cells if name not in names]
        if unknown:
            raise TableError(f"정의하지 않은 칸이다: {', '.join(unknown)}")
        if not self._groups:
            self.group()
        self._groups[-1][2].append(Row(ref, MappingProxyType(dict(cells))))
        return self

    def summary(self, values: Mapping[str, str]) -> Self:
        self._summary.update(values)
        return self

    def step(self, text: str) -> Self:
        self._steps.append(text)
        return self

    def steps(self, texts: Iterable[str]) -> Self:
        self._steps.extend(texts)
        return self

    def row_formats(self, formats: Sequence[str]) -> Self:
        self._row_formats = list(formats)
        return self

    def build(self) -> WorkTable:
        groups = tuple(
            Group(title, tuple(rows), MappingProxyType(summary))
            for title, summary, rows in self._groups
        )
        return WorkTable(
            columns=tuple(self._columns),
            groups=groups,
            summary=MappingProxyType(dict(self._summary)),
            steps=tuple(self._steps),
            subject=self._subject,
            unit=self._unit,
            row_formats=tuple(self._row_formats),
        )


# 만든 지 이만큼 지난 표는 맥락과 무관하게 지운다(그 채널에서 다시 멘션하지 않으면 지울 계기가 없다).
TABLE_TTL_SECONDS = 24 * 60 * 60


@dataclass(slots=True)
class _StoredTable:
    table: WorkTable
    created_at: float
    stale: bool = False
    message_id: str | None = None
    bound_seq: int = 0


class TableStore:
    """작업 표 보관소. 메모리에만 두고, 키는 (플레이어 id, 채널 id)다. 재시작하면 사라진다.

    표는 바꾸지 않는다. "옛 값" 표시도 표 객체가 아니라 이 보관소가 따로 기억한다."""

    def __init__(self) -> None:
        self._tables: dict[tuple[str, str], dict[str, _StoredTable]] = {}
        # 번호는 지운 뒤에도 되살리지 않는다 — 사라진 표의 id가 다른 표를 가리키는 일이 없게 한다.
        self._next_number: dict[tuple[str, str], int] = {}
        self._bind_seq = 0

    def put(
        self, player_id: str, channel_id: str, table: WorkTable, *, now: float | None = None, stale: bool = False
    ) -> str:
        """표를 보관하고 id(`t1`부터 그 플레이어+채널에서 늘어난다)를 붙여 돌려준다."""
        key = (player_id, channel_id)
        number = self._next_number.get(key, 0) + 1
        self._next_number[key] = number
        table_id = f"t{number}"
        created_at = time.time() if now is None else now
        self._tables.setdefault(key, {})[table_id] = _StoredTable(
            replace(table, id=table_id), created_at, stale=stale
        )
        return table_id

    def get(self, player_id: str, channel_id: str, table_id: str) -> WorkTable | None:
        stored = self._tables.get((player_id, channel_id), {}).get(table_id)
        return None if stored is None else stored.table

    def ids(self, player_id: str, channel_id: str) -> list[str]:
        """그 플레이어+채널의 살아 있는 표 id를 만든 순서대로."""
        return list(self._tables.get((player_id, channel_id), {}))

    def is_stale(self, player_id: str, channel_id: str, table_id: str) -> bool:
        stored = self._tables.get((player_id, channel_id), {}).get(table_id)
        return stored is not None and stored.stale

    def mark_stale(self, subject_player_id: str) -> None:
        """모든 채널에서 `subject`가 그 플레이어인 표에 "옛 값" 표시를 단다."""
        for tables in self._tables.values():
            for stored in tables.values():
                if stored.table.subject == subject_player_id:
                    stored.stale = True

    def bind(self, player_id: str, channel_id: str, table_id: str, message_id: str) -> None:
        """출력한 표를 유니 답장 메시지에 묶는다."""
        stored = self._tables.get((player_id, channel_id), {}).get(table_id)
        if stored is None:
            return
        self._bind_seq += 1
        stored.message_id = message_id
        stored.bound_seq = self._bind_seq

    def table_for_message(self, player_id: str, channel_id: str, message_id: str) -> str | None:
        for table_id, stored in self._tables.get((player_id, channel_id), {}).items():
            if stored.message_id == message_id:
                return table_id
        return None

    def last_bound(self, player_id: str, channel_id: str) -> str | None:
        """가장 최근에 메시지에 묶인 표."""
        best: tuple[int, str] | None = None
        for table_id, stored in self._tables.get((player_id, channel_id), {}).items():
            if stored.message_id is not None and (best is None or stored.bound_seq > best[0]):
                best = (stored.bound_seq, table_id)
        return None if best is None else best[1]

    def retain_only(self, player_id: str, channel_id: str, alive_message_ids: Collection[str]) -> None:
        """맥락 안 메시지에 묶인 표만 남기고 지운다. 묶이지 않은 표도 지운다."""
        tables = self._tables.get((player_id, channel_id))
        if not tables:
            return
        for table_id in [
            table_id
            for table_id, stored in tables.items()
            if stored.message_id is None or stored.message_id not in alive_message_ids
        ]:
            del tables[table_id]

    def discard(self, player_id: str, channel_id: str, table_id: str) -> None:
        """표 하나를 지운다(이 턴의 마지막이 아닌 중간 표)."""
        tables = self._tables.get((player_id, channel_id))
        if tables is not None:
            tables.pop(table_id, None)

    def drop_channel(self, channel_id: str) -> None:
        """그 채널의 표를 모든 플레이어 몫으로 지운다(대화 스레드가 닫힐 때)."""
        for key in [key for key in self._tables if key[1] == channel_id]:
            del self._tables[key]

    def expire(self, now: float) -> int:
        """만든 지 24시간이 지난 표를 지우고 지운 개수를 돌려준다."""
        removed = 0
        for key in list(self._tables):
            tables = self._tables[key]
            for table_id in [t for t, stored in tables.items() if now - stored.created_at >= TABLE_TTL_SECONDS]:
                del tables[table_id]
                removed += 1
            if not tables:
                del self._tables[key]
        return removed
