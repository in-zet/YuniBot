from pathlib import Path

from core.registry import SkillRegistry
from scripts.eval_agent import (
    EvalCase,
    TurnTrace,
    _input_table_ids,
    collect_eval_cases,
    judge_example,
    judge_scenario,
)
from scripts.eval_scenarios import SCENARIOS, Scenario


def test_collect_eval_cases_includes_all_examples() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    cases = collect_eval_cases(registry)
    border_cases = [c for c in cases if c.skill_name == "chunithm-border"]
    assert len(border_cases) == 3  # chunithm-border/SKILL.md의 examples 3개


def test_judge_example_passes_when_any_tool_of_the_skill_was_called() -> None:
    case = EvalCase(skill_name="chunithm-border", question="크로노 마스 보더")
    assert judge_example(case, TurnTrace(calls=[("song_chart", {}), ("chunithm-border_add_borders", {})])) == []
    assert judge_example(case, TurnTrace(calls=[("song_info", {})]))


def test_judge_scenario_checks_where_the_shown_table_came_from() -> None:
    scenario = Scenario(name="상수표", question="q", shown_from=("report_const_table",), must_call=("image_table_image",))
    good = TurnTrace(
        calls=[("song_find_songs", {}), ("report_const_table", {"table": "t1"}), ("image_table_image", {"table": "t2"})],
        lineage={"t1": {"song_find_songs"}, "t2": {"song_find_songs", "report_const_table"}},
        shown_table_ids=["t2"],
    )
    assert judge_scenario(scenario, good) == []
    bad = TurnTrace(
        calls=[("song_find_songs", {}), ("image_table_image", {"table": "t1"})],
        lineage={"t1": {"song_find_songs"}}, shown_table_ids=["t1"],
    )
    assert judge_scenario(scenario, bad)
    assert judge_scenario(scenario, TurnTrace(calls=[("image_table_image", {})]))  # 보인 표가 없다


def test_judge_scenario_must_and_must_not_call() -> None:
    scenario = Scenario(name="s", question="q", must_call_any=("a", "b"), must_not_call=("c",))
    assert judge_scenario(scenario, TurnTrace(calls=[("b", {})])) == []
    assert judge_scenario(scenario, TurnTrace(calls=[("b", {}), ("c", {})]))
    assert judge_scenario(scenario, TurnTrace(calls=[("d", {})]))


def test_input_table_ids_reads_table_args_only() -> None:
    assert _input_table_ids({"table": "t3:2,4", "rank": "SSS", "min_const": 15.0}) == ["t3"]


def test_scenario_names_are_unique() -> None:
    names = [s.name for s in SCENARIOS]
    assert len(names) == len(set(names))
