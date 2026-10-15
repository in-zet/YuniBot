from __future__ import annotations

from typing import Literal

Mode = Literal["instructions", "full"]
Level = Literal["card", "instructions", "full"]


class SkillLoadState:
    """한 번의 에이전트 반복(멘션 하나) 동안 어느 스킬이 '펼쳐졌는지' 추적한다.

    instructions 모드는 모든 스킬이 처음부터 'instructions' 수준(카드+본문 지침, 도구 정의는
    core/prompt.py가 instructions 모드일 때 전부 함께 넣는다)이고 open_skill은 'full'로 올린다.
    full 모드는 모든 스킬이 'card' 수준(카드만)에서 시작하고 open_skill이 'full'로 올린다.
    대화(답장 사슬)를 넘어서는 유지는 하지 않는다 — 00-core.md "스킬 불러오기"의 범위를
    이번 단계에서는 한 번의 에이전트 반복으로 좁힌 결정(결정 사항 B-5)에 따른 것이다.
    """

    def __init__(self, *, mode: Mode, known_skills: set[str]) -> None:
        self._mode = mode
        self._known_skills = known_skills
        self._opened: set[str] = set()

    def open(self, skill_name: str) -> None:
        self._opened.add(skill_name)

    def level_for(self, skill_name: str) -> Level:
        if skill_name not in self._known_skills:
            return "card"
        if skill_name in self._opened:
            return "full"
        return "instructions" if self._mode == "instructions" else "card"

    def opened_skills(self) -> set[str]:
        return set(self._opened)
