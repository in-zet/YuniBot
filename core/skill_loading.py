from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

Mode = Literal["instructions", "full"]
Level = Literal["card", "instructions", "full"]

# 이전 메시지에서 이어 받아 펼쳐 둘 수 있는 스킬 수. 넘치면 이어 받은 것 중 가장 오래 안 쓴 것부터 접는다
# (00-core.md "스킬 불러오기"). 이번 요청에서 펼친 스킬은 이 수와 상관없이 요청이 끝날 때까지 접지 않는다.
MAX_OPEN_SKILLS = 5


class SkillLoadState:
    """한 요청 동안 어느 스킬이 '펼쳐졌는지' 추적한다. 요청마다 새로 만든다.

    instructions 모드는 모든 스킬이 처음부터 'instructions' 수준(카드+본문 지침, 도구 정의는
    core/prompt.py가 instructions 모드일 때 전부 함께 넣는다)이고 open_skill은 'full'로 올린다.
    full 모드는 모든 스킬이 'card' 수준(카드만)에서 시작하고 open_skill이 'full'로 올린다.
    이전 메시지에서 펼쳐 둔 스킬은 `opened`로 받아 처음부터 펼친 채로 시작한다. `keep`(대화 스레드를 연 스킬)과
    이번 요청에서 펼치거나 쓴 스킬은 요청 도중에 접지 않는다 — 2026-11-28 실기: 펼친 스킬이 상한을 넘자 `open_skill`
    하나가 이미 쓰던 report를 접어, 요청 도중에 플카 도구가 사라졌고 모델이 답을 지어냈다.
    """

    def __init__(
        self,
        *,
        mode: Mode,
        known_skills: set[str],
        opened: Iterable[str] = (),
        keep: Iterable[str] = (),
        max_open: int = MAX_OPEN_SKILLS,
    ) -> None:
        self._mode = mode
        self._known_skills = known_skills
        self._max_open = max_open
        # dict의 삽입 순서가 곧 "오래 안 쓴 순서"다(맨 앞이 가장 오래된 것).
        self._opened: dict[str, None] = {}
        # 이번 요청에서 접으면 안 되는 스킬.
        self._pinned: set[str] = {name for name in keep if name in known_skills}
        for name in (*opened, *self._pinned):
            if name in known_skills:
                _ = self._opened.pop(name, None)
                self._opened[name] = None
        self._fold_over_cap()

    def open(self, skill_name: str) -> None:
        _ = self._opened.pop(skill_name, None)
        self._opened[skill_name] = None
        self._pinned.add(skill_name)
        self._fold_over_cap()

    def touch(self, skill_name: str) -> None:
        """펼쳐 둔 스킬의 도구를 썼다는 표시(이번 요청 동안 접지 않는다). 펼치지 않은 스킬은 건드리지 않는다."""
        if skill_name in self._opened:
            self.open(skill_name)

    def _fold_over_cap(self) -> None:
        """상한을 넘으면 이어 받은 스킬 중 오래 안 쓴 것부터 접는다. 이번 요청의 스킬만 남으면 상한을 넘어도 그대로 둔다."""
        while len(self._opened) > self._max_open:
            victim = next((name for name in self._opened if name not in self._pinned), None)
            if victim is None:
                return
            del self._opened[victim]

    def level_for(self, skill_name: str) -> Level:
        if skill_name not in self._known_skills:
            return "card"
        if skill_name in self._opened:
            return "full"
        return "instructions" if self._mode == "instructions" else "card"

    def opened_skills(self) -> set[str]:
        return set(self._opened)

    def ordered_skills(self) -> list[str]:
        """오래 안 쓴 것부터의 순서. 다음 메시지로 넘길 때 이 순서를 그대로 보존한다(상한은 다음 요청이 적용한다)."""
        return list(self._opened)
