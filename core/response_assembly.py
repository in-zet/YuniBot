from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from core.agent import AgentTurnResult
from core.numeric_check import unverified_note
from core.tool_types import Display, table_display

logger = logging.getLogger(__name__)

_SHOW_TAG_RE = re.compile(r"\[\[show:([A-Za-z0-9_-]+)\]\]")
# 유니가 답에 쓰면 이 요청의 마지막 작업 표를 글자로 붙이지 않는다.
_NO_TABLE_TAG = "[[no_table]]"


@dataclass(slots=True, kw_only=True)
class AssembledResponse:
    text: str
    displays: list[Display] = field(default_factory=list)
    # 말 속 숫자 중 어디서도 확인 안 된 것. 비어 있지 않으면 숫자 규칙 위반이다.
    unknown_numbers: set[float] = field(default_factory=set)

    @property
    def numeric_violation(self) -> bool:
        return bool(self.unknown_numbers)


def assemble_response(result: AgentTurnResult) -> AssembledResponse:
    text = result.text or ""
    unknown = result.unknown_numbers(text)
    if unknown:
        # 하드 제약 위반(LLM이 어디서도 확인 안 된 숫자를 말함). 에이전트가 이미 한 번 다시 쓰게 했는데도 남은 것이라
        # ERROR로 남기고, 답 끝에 그 숫자를 표시한다(core/numeric_check.py).
        logger.error("numeric rule violation: unknown=%s", sorted(unknown))

    requested_ids = set(_SHOW_TAG_RE.findall(text))
    omit_tables = _NO_TABLE_TAG in text
    clean_text = _SHOW_TAG_RE.sub("", text).replace(_NO_TABLE_TAG, "").strip()
    if unknown and clean_text:
        clean_text = f"{clean_text}\n{unverified_note(unknown)}"

    # 숫자 위반이어도 그 턴에 모인 optional 표시물을 전부 보여주지 않는다 — 실기 2026-10-18:
    # 목표 하나만 수정했는데 이름→id 해석용으로 불렀을 뿐인 list_goals까지 통째로 새어 나왔다.
    # `[[show:<id>]]`로 모델이 명시한 것만 보여준다(위반은 로그와 숫자 표시로만 남는다).
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

    return AssembledResponse(text=clean_text, displays=displays, unknown_numbers=unknown)
