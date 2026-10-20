# core/tables.py
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
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
