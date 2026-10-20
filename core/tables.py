# core/tables.py
from __future__ import annotations

import time
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
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
