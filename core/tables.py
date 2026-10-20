# core/tables.py
from __future__ import annotations

import re
import time
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Literal, Self

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
