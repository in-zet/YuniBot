"""평가 질문이 필요한 스킬을 **트리거로** 펼치는지 본다(2026-11-28 실기 사고 뒤).

운영(`SKILL_LOADING_MODE=full`)에서 스킬 도구는 메시지의 트리거 낱말로 미리 펼쳐지거나 모델이 `open_skill`을 불러야만
보인다. 평가에서는 모델이 스스로 `open_skill`을 불러 통과했지만 운영에서는 안 불러, 플카 도구 없이 답을 지어냈다.
그래서 시나리오·예시가 기대하는 도구의 스킬은 모델의 선택에 기대지 않고 트리거로 펼쳐져야 한다 — 이 시험이 막는다.
"""
from pathlib import Path

import pytest

from core.prompt import PromptAssembler
from core.registry import SkillRegistry
from scripts.eval_scenarios import SCENARIOS, Scenario

# 트리거로 펼칠 수 없어 모델의 `open_skill`에 맡기는 시나리오와 그 까닭. 늘리기 전에 트리거로 될지 먼저 본다.
_EXEMPT = {
    # 곡 이름 해석이 실패한 뒤에만 곡 검색이 필요하다(규칙의 "못 알아들으면" 줄). 곡 이름엔 공통 낱말이 없다.
    "곡 찾기/한글 이름을 한자로": "song",
    # 튜토리얼 DM 대화 안의 답이라 운영에서는 대화 스레드를 연 스킬이 이미 펼쳐져 있다.
    "튜토리얼/주 게임 답에 한 단계만": "tutorial",
}


def _registry() -> SkillRegistry:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    return registry


def _assembler(registry: SkillRegistry) -> PromptAssembler:
    return PromptAssembler(rules_text="", persona_text="", registry=registry)


def _all_tags(registry: SkillRegistry) -> set[str]:
    """태그로 가려지는 스킬까지 다 보이게(평가 플레이어는 로그인·동기화·admin을 다 가진다)."""
    return {tag for skill in registry.enabled_skills() for tag in (*skill.manifest.tags, *skill.manifest.tags_any)}


def _owner(tool: str, names: list[str]) -> str | None:
    """도구 이름("report_add_scores")의 스킬. 코어 도구(`table_query` 등)는 None."""
    for name in sorted(names, key=len, reverse=True):
        if tool.startswith(f"{name}_"):
            return name
    return None


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_scenario_question_triggers_the_skills_its_tools_need(scenario: Scenario) -> None:
    registry = _registry()
    names = [skill.manifest.name for skill in registry.enabled_skills()]
    triggered = set(_assembler(registry).triggered_skills(scenario.question, player_tags=_all_tags(registry)))

    needed = {owner for tool in (*scenario.must_call, *scenario.shown_from) if (owner := _owner(tool, names))}
    needed.discard(_EXEMPT.get(scenario.name, ""))
    assert needed <= triggered, f"트리거로 안 펼쳐지는 스킬: {sorted(needed - triggered)} — 그 스킬 triggers에 낱말을 더한다"

    any_of = {owner for tool in scenario.must_call_any if (owner := _owner(tool, names))}
    assert not any_of or any_of & triggered, f"트리거로 안 펼쳐지는 스킬: {sorted(any_of)} 중 하나"


def test_every_skill_example_triggers_its_own_skill() -> None:
    registry = _registry()
    assembler = _assembler(registry)
    tags = _all_tags(registry)
    missing = [
        (skill.manifest.name, example)
        for skill in registry.enabled_skills()
        for example in skill.manifest.examples
        if skill.manifest.name not in assembler.triggered_skills(example, player_tags=tags)
    ]
    assert not missing


def test_exemptions_name_real_scenarios() -> None:
    assert set(_EXEMPT) <= {scenario.name for scenario in SCENARIOS}
