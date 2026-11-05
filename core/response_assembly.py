from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.agent import AgentTurnResult
from core.tables import WorkTable
from core.tool_types import Display, table_display

logger = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_SHOW_TAG_RE = re.compile(r"\[\[show:([A-Za-z0-9_-]+)\]\]")
# 유니가 답에 쓰면 이 요청의 마지막 작업 표를 글자로 붙이지 않는다.
_NO_TABLE_TAG = "[[no_table]]"
_IGNORED = {0.0, 1.0}
# 소수점·쉼표 없는 작은 정수(순위 번호, "베스트 30"·"신곡 20" 같은 프레임 크기, 날짜, 곡 수)는 유니가
# 계산해 만들어 낸 숫자가 아니라 말투·목록의 일부라 대조에서 뺀다(2026-10-19: 목록 번호와 프레임 크기가
# 매번 위반으로 잡혔다). 점수·레이팅·상수·허용 어택은 소수나 큰 수라 그대로 대조한다.
_MAX_FREE_INTEGER = 50


def extract_numbers(text: str) -> set[float]:
    numbers: set[float] = set()
    for match in _NUMBER_RE.finditer(text):
        raw = match.group().rstrip(",")  # "30, 20"의 쉼표가 숫자에 붙어 잡히는 것을 막는다
        value = float(raw.replace(",", ""))
        if value in _IGNORED:
            continue
        if raw.lstrip("-").isdigit() and abs(value) <= _MAX_FREE_INTEGER:
            continue
        numbers.add(value)
    return numbers


def flatten_numbers(data: list[dict[str, Any]]) -> set[float]:
    numbers: set[float] = set()

    def _walk(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            numbers.add(float(value))
        elif isinstance(value, str):
            numbers.update(extract_numbers(value))
        elif isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, (list, tuple)):
            for v in value:
                _walk(v)

    for entry in data:
        _walk(entry)
    return numbers


def table_numbers(table: WorkTable) -> set[float]:
    """표에 찍히는 숫자 전부(칸 값·칸 글자·묶음 이름·요약). 도구가 만든 값이라 유니가 말해도 된다."""
    numbers: set[float] = set()
    texts = [*table.summary.values()]
    for group in table.groups:
        texts.extend([group.title or "", *group.summary.values()])
        for row in group.rows:
            for value in row.cells.values():
                texts.append(value.text)
                if isinstance(value.raw, (int, float)) and not isinstance(value.raw, bool):
                    numbers.add(float(value.raw))
    for text in texts:
        numbers.update(extract_numbers(text))
    return numbers


@dataclass(slots=True, kw_only=True)
class AssembledResponse:
    text: str
    displays: list[Display] = field(default_factory=list)
    # 말 속 숫자 중 어디서도 확인 안 된 것. 비어 있지 않으면 숫자 규칙 위반이다.
    unknown_numbers: set[float] = field(default_factory=set)

    @property
    def numeric_violation(self) -> bool:
        return bool(self.unknown_numbers)


def assemble_response(result: AgentTurnResult, *, conversation_text: str = "") -> AssembledResponse:
    """conversation_text: 모델이 본 대화 글. 사용자가 말한 숫자나 앞서 보인 숫자를 되풀이하는 것은
    숫자를 지어낸 게 아니라 맞는 숫자로 친다(2026-11-05: 이것과 표 칸 값을 안 봐서 정상 답이 위반으로 잡혔다)."""
    text = result.text or ""
    known_numbers = flatten_numbers(result.collected_data) | extract_numbers(conversation_text)
    for table in result.turn_tables:
        known_numbers |= table_numbers(table)
    displays_text = [d.content or "" for d in (*result.required_displays, *result.optional_displays.values())]
    for tool_text in (*result.tool_errors, *displays_text):
        known_numbers |= extract_numbers(tool_text)
    unknown_numbers = extract_numbers(text) - known_numbers
    if unknown_numbers:
        # 하드 제약 위반(LLM이 어디서도 확인 안 된 숫자를 말함)이라 ERROR로 남긴다.
        logger.error("numeric rule violation: unknown=%s", sorted(unknown_numbers))

    requested_ids = set(_SHOW_TAG_RE.findall(text))
    omit_tables = _NO_TABLE_TAG in text
    clean_text = _SHOW_TAG_RE.sub("", text).replace(_NO_TABLE_TAG, "").strip()

    # 숫자 위반이어도 그 턴에 모인 optional 표시물을 전부 보여주지 않는다 — 실기 2026-10-18:
    # 목표 하나만 수정했는데 이름→id 해석용으로 불렀을 뿐인 list_goals까지 통째로 새어 나왔다.
    # `[[show:<id>]]`로 모델이 명시한 것만 보여준다(위반은 경고 로그로만 남는다).
    optional = [result.optional_displays[i] for i in requested_ids if i in result.optional_displays]

    # required 표시물은 숫자 위반 여부와 무관하게 항상 붙는다(도구의 정상적인 주 출력이지,
    # 위반의 "증거"가 아니다) — 그래서 위반 게이트를 거치지 않고, 강제로 보여줄 것들보다
    # 먼저 온다.
    required = list(result.required_displays)
    # 턴의 마지막 표 하나만 글자로 그린다. 그림 표로 이미 그렸으면 다시 그리지 않는다.
    table = result.last_table
    if (
        table is not None and not omit_tables and result.last_table_id not in result.drawn_table_ids
    ):
        required.append(table_display(table, stale=result.last_table_stale))
    displays = required + optional

    return AssembledResponse(text=clean_text, displays=displays, unknown_numbers=unknown_numbers)
