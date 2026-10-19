from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

Mode = Literal["instructions", "full"]
Level = Literal["card", "instructions", "full"]

# 동시에 펼쳐 둘 수 있는 스킬 수. 넘치면 가장 오래 안 쓴 것부터 접는다(00-core.md "스킬 불러오기").
MAX_OPEN_SKILLS = 5


class SkillLoadState:
    """한 대화(답장 사슬이나 대화 스레드) 동안 어느 스킬이 '펼쳐졌는지' 추적한다.

    instructions 모드는 모든 스킬이 처음부터 'instructions' 수준(카드+본문 지침, 도구 정의는
    core/prompt.py가 instructions 모드일 때 전부 함께 넣는다)이고 open_skill은 'full'로 올린다.
    full 모드는 모든 스킬이 'card' 수준(카드만)에서 시작하고 open_skill이 'full'로 올린다.
    이전 메시지에서 펼쳐 둔 스킬은 `opened`로 받아 처음부터 펼친 채로 시작한다.
    """

    def __init__(
        self,
        *,
        mode: Mode,
        known_skills: set[str],
        opened: Iterable[str] = (),
        max_open: int = MAX_OPEN_SKILLS,
    ) -> None:
        self._mode = mode
        self._known_skills = known_skills
        self._max_open = max_open
        # dict의 삽입 순서가 곧 "오래 안 쓴 순서"다(맨 앞이 가장 오래된 것).
        self._opened: dict[str, None] = {}
        for name in opened:
            if name in known_skills:
                self.open(name)

    def open(self, skill_name: str) -> None:
        _ = self._opened.pop(skill_name, None)
        self._opened[skill_name] = None
        while len(self._opened) > self._max_open:
            del self._opened[next(iter(self._opened))]

    def touch(self, skill_name: str) -> None:
        """펼쳐 둔 스킬의 도구를 썼다는 표시. 펼치지 않은 스킬은 건드리지 않는다."""
        if skill_name in self._opened:
            self.open(skill_name)

    def level_for(self, skill_name: str) -> Level:
        if skill_name not in self._known_skills:
            return "card"
        if skill_name in self._opened:
            return "full"
        return "instructions" if self._mode == "instructions" else "card"

    def opened_skills(self) -> set[str]:
        return set(self._opened)

    def ordered_skills(self) -> list[str]:
        """오래 안 쓴 것부터의 순서. 다음 메시지로 넘길 때 이 순서를 그대로 보존한다."""
        return list(self._opened)
