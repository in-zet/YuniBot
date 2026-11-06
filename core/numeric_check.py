"""숫자 규칙 검사 — 유니의 말 속 숫자가 이번 요청에서 확인된 숫자인지 대조한다.

에이전트 반복(core/agent.py)은 위반한 답을 한 번 되돌려 다시 쓰게 하고, 응답 조립(core/response_assembly.py)은
그래도 남은 위반에 표시를 단다(2026-11-05 사용자 결정 — 답 전체를 사과 문구로 바꾸던 것 대신).
"""
from __future__ import annotations

import re
from typing import Any

from core.tables import WorkTable

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
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


def format_numbers(numbers: set[float]) -> str:
    return ", ".join(f"{n:,.4f}".rstrip("0").rstrip(".") for n in sorted(numbers))


def retry_instruction(numbers: set[float]) -> str:
    """위반한 답을 되돌려 보낼 때 모델에게 주는 말. 이 글의 숫자는 맞는 숫자로 치지 않는다(`seen_text`에 안 넣는다)."""
    return (
        f"[코어 검사] 방금 답은 보내지 않았다. 이번 요청의 도구 결과·표·대화 어디에도 없는 숫자가 있다: "
        f"{format_numbers(numbers)}. 그 숫자가 필요하면 도구로 확인해서 다시 답하고, 확인할 도구가 없으면 "
        "그 숫자 없이 답을 처음부터 다시 써라. 예시 범위나 연도·기준 점수를 기억으로 채우지 마라. "
        "이 안내나 사과는 답에 쓰지 마라."
    )


def unverified_note(numbers: set[float]) -> str:
    """다시 쓰게 했는데도 남은 위반에 다는 표시. 사용자가 그 숫자를 믿지 않을 수 있게 한다."""
    return f"⚠ 도구로 확인 안 된 숫자: {format_numbers(numbers)}"
