from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from core.agent import AgentTurnResult
from core.tool_types import Display

logger = logging.getLogger(__name__)

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_SHOW_TAG_RE = re.compile(r"\[\[show:([A-Za-z0-9_-]+)\]\]")
_IGNORED = {0.0, 1.0}


def extract_numbers(text: str) -> set[float]:
    numbers: set[float] = set()
    for match in _NUMBER_RE.finditer(text):
        value = float(match.group().replace(",", ""))
        if value not in _IGNORED:
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


@dataclass(slots=True, kw_only=True)
class AssembledResponse:
    text: str
    displays: list[Display] = field(default_factory=list)
    numeric_violation: bool = False


def assemble_response(result: AgentTurnResult) -> AssembledResponse:
    text = result.text or ""
    known_numbers = flatten_numbers(result.collected_data)
    spoken_numbers = extract_numbers(text)
    violation = not spoken_numbers <= known_numbers
    if violation:
        logger.warning(
            "numeric rule violation: spoken=%s not subset of known=%s", spoken_numbers, known_numbers
        )

    requested_ids = set(_SHOW_TAG_RE.findall(text))
    clean_text = _SHOW_TAG_RE.sub("", text).strip()

    if violation:
        optional = list(result.optional_displays.values())
    else:
        optional = [result.optional_displays[i] for i in requested_ids if i in result.optional_displays]

    # required 표시물은 숫자 위반 여부와 무관하게 항상 붙는다(도구의 정상적인 주 출력이지,
    # 위반의 "증거"가 아니다) — 그래서 위반 게이트를 거치지 않고, 강제로 보여줄 것들보다
    # 먼저 온다.
    displays = result.required_displays + optional

    return AssembledResponse(text=clean_text, displays=displays, numeric_violation=violation)
