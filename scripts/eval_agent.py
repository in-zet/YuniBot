"""도구 선택(해석) 평가 명령. 운영과 같은 제공자·모델(.env의 LLM_PROVIDER·AGENT_MODEL 등)로 실제 LLM을 부르므로
키가 필요하고 비용이 든다. uv run pytest에는 수집되지 않는다.

운영과 같은 배선(core.bot.build_client)을 임시 DB로 띄워 실제 서비스·도구·표 보관소를 그대로 쓴다.
운영 DB(DB_PATH)는 건드리지 않는다. 곡 DB는 운영처럼 기동 때 받아 온다. 평가 플레이어는 기록이 없고
CHUNITHM-NET에 접속하지 않는다.

두 묶음:
- examples: 스킬 `examples` 질문마다 그 스킬의 도구가 한 번이라도 불렸는지.
- scenarios: `scripts/eval_scenarios.py`의 시나리오마다 불린 도구와 사용자에게 보인 표의 출처(계보).

사용법:
    PYTHONPATH=.:third_party uv run python3 scripts/eval_agent.py                 # 둘 다
    PYTHONPATH=.:third_party uv run python3 scripts/eval_agent.py --suite scenarios --only 상수표 --repeat 3
"""
from __future__ import annotations

import argparse
import asyncio
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from core.agent import AgentLoop, AgentTurnResult
from core.bot import YuniClient, build_client
from core.config import CoreConfig, load_config
from core.executor import ToolExecutor
from core.llm import LlmAdapter
from core.llm_claude import ClaudeLlmAdapter
from core.llm_gemini import GeminiLlmAdapter
from core.llm_pricing import estimate_cost_usd
from core.prompt import PromptAssembler
from core.registry import SkillRegistry
from core.registry_snapshot import build_snapshot
from core.response_assembly import assemble_response
from core.skill_loading import SkillLoadState
from core.tables import TableError, parse_table_arg
from core.tool_types import ToolContext, ToolResult
from scripts.eval_scenarios import SCENARIOS, Scenario
from services.common.players import PlayersService

_EVAL_CHANNEL = "eval"


@dataclass(slots=True, kw_only=True, frozen=True)
class EvalCase:
    skill_name: str
    question: str


@dataclass(slots=True, kw_only=True)
class TurnTrace:
    """한 턴에 불린 도구와 표 계보. 계보는 표 id → 그 표를 만들기까지 거친 도구 이름들."""

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    lineage: dict[str, set[str]] = field(default_factory=dict)
    shown_table_ids: list[str] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [name for name, _ in self.calls]


@dataclass(slots=True, kw_only=True)
class EvalReport:
    total: int = 0
    failures: int = 0
    numeric_violations: int = 0
    latencies_seconds: list[float] = field(default_factory=list)
    estimated_cost_usd: float = 0.0


def collect_eval_cases(registry: SkillRegistry) -> list[EvalCase]:
    """도구 선택 평가 대상. LLM이 부를 수 있는 도구가 하나도 없는 스킬(전부 slash_only,
    예: account-link)은 뺀다 — 그런 스킬의 examples는 애초에 도구 호출로 통과할 길이 없다."""
    cases: list[EvalCase] = []
    for skill in registry.enabled_skills():
        if not skill.code.tools:
            print(f"[스킵] {skill.manifest.name}: LLM 도구가 없다(슬래시 전용)")
            continue
        cases.extend(EvalCase(skill_name=skill.manifest.name, question=example) for example in skill.manifest.examples)
    return cases


def all_manifest_tags(registry: SkillRegistry) -> frozenset[str]:
    """평가용 가짜 플레이어에게 줄 태그. 등록된 스킬이 요구하는 태그를 모두 모아 쥐여 줘서
    (실제 서버엔 이 조합을 가진 사람이 없을 수 있지만) 태그 때문에 도구 자체가 안 보여 평가가
    뭉개지는 것을 막는다(각 스킬의 manifest.tags/tags_any)."""
    tags: set[str] = set()
    for skill in registry.enabled_skills():
        tags.update(skill.manifest.tags)
        tags.update(skill.manifest.tags_any)
    return frozenset(tags)


def judge_example(case: EvalCase, trace: TurnTrace) -> list[str]:
    """examples는 그 스킬 도구가 한 번이라도 불렸으면 통과다(낱말 트리거로 다른 스킬이 먼저 불릴 수 있다)."""
    if any(name.startswith(f"{case.skill_name}_") for name in trace.tool_names):
        return []
    return [f"{case.skill_name} 도구가 안 불렸다"]


def judge_scenario(scenario: Scenario, trace: TurnTrace) -> list[str]:
    problems: list[str] = []
    called = set(trace.tool_names)
    if scenario.must_call_any and not called & set(scenario.must_call_any):
        problems.append(f"{'/'.join(scenario.must_call_any)} 중 아무것도 안 불렸다")
    problems.extend(f"{name}이 안 불렸다" for name in scenario.must_call if name not in called)
    problems.extend(f"{name}이 불렸다" for name in scenario.must_not_call if name in called)
    if scenario.shown_from:
        if not trace.shown_table_ids:
            problems.append("보인 표가 없다")
        for table_id in trace.shown_table_ids:
            origin = trace.lineage.get(table_id, set())
            if not origin & set(scenario.shown_from):
                problems.append(f"보인 표 {table_id}의 출처 {sorted(origin)}에 {'/'.join(scenario.shown_from)}가 없다")
    return problems


def _input_table_ids(args: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for value in args.values():
        if isinstance(value, str):
            try:
                ids.append(parse_table_arg(value)[0])
            except TableError:
                continue
    return ids


class _TracingAgentLoop(AgentLoop):
    """불린 도구와 표 계보를 기록한다(평가 전용, core/agent.py는 안 바꾼다)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.trace = TurnTrace()
        self._current: tuple[str, dict[str, Any]] | None = None

    async def _invoke(self, ctx: ToolContext, tool_name: str, args: dict[str, Any], **kwargs: Any) -> ToolResult:  # type: ignore[override]
        self._current = (tool_name, dict(args))
        self.trace.calls.append(self._current)
        return await super()._invoke(ctx, tool_name, args, **kwargs)

    def _keep_table(self, ctx: ToolContext, tool_result: ToolResult, turn: AgentTurnResult) -> ToolResult:
        kept = super()._keep_table(ctx, tool_result, turn)
        if self._current is not None and turn.last_table_id is not None and kept.error is None:
            name, args = self._current
            origin = {name}
            for source in _input_table_ids(args):
                origin |= self.trace.lineage.get(source, set())
            self.trace.lineage[turn.last_table_id] = origin
        return kept


async def _run_turn(
    *, config: CoreConfig, llm: LlmAdapter, prompt: PromptAssembler, executor: ToolExecutor, client: YuniClient,
    registry: SkillRegistry, player_id: str, tags: frozenset[str], question: str,
) -> tuple[AgentTurnResult, TurnTrace, float]:
    loop = _TracingAgentLoop(
        llm=llm, executor=executor, prompt=prompt, model=config.agent_model, thinking_level=config.thinking_level,
        max_tool_calls=config.max_tool_calls_per_turn, max_tokens=config.max_tokens_per_turn, db=client.conn,
        registry_snapshot=build_snapshot(registry),
    )
    ctx = ToolContext(
        player_id=player_id, tags=tags, services={}, event_bus=executor.event_bus,
        usage=cast("Any", executor.services["usage"]), tables=executor.tables, channel_id=_EVAL_CHANNEL,
    )
    state = SkillLoadState(mode=config.skill_loading_mode, known_skills={s.manifest.name for s in registry.enabled_skills()})
    started = time.monotonic()
    result = await loop.run(ctx=ctx, skill_state=state, user_text=question, prompt_context_snippets=[])
    elapsed = time.monotonic() - started
    trace = loop.trace
    trace.shown_table_ids = list(result.drawn_table_ids) or ([result.last_table_id] if result.last_table_id else [])
    return result, trace, elapsed


def _llm_for(config: CoreConfig) -> ClaudeLlmAdapter | GeminiLlmAdapter:
    if config.llm_provider == "claude":
        if not config.anthropic_api_key:
            raise SystemExit("ANTHROPIC_API_KEY가 없다")
        return ClaudeLlmAdapter(api_key=config.anthropic_api_key)
    if not config.gemini_api_key:
        raise SystemExit("GEMINI_API_KEY가 없다")
    return GeminiLlmAdapter(api_key=config.gemini_api_key, fallback_model=config.fallback_model or None)


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", default="all", choices=["all", "examples", "scenarios"])
    parser.add_argument("--only", help="이 글자가 이름(시나리오)·질문(examples)에 든 것만")
    parser.add_argument("--repeat", type=int, default=1, help="같은 질문을 몇 번 돌릴지(답이 흔들리는지 보려면 2 이상)")
    parser.add_argument("--model", help="AGENT_MODEL 대신 쓸 모델(같은 제공자)")
    args = parser.parse_args()

    base = load_config()
    with tempfile.TemporaryDirectory(prefix="yuni_eval_") as tmp:
        config = replace(base, db_path=Path(tmp) / "eval.db", test_guild_id=None, agent_model=args.model or base.agent_model)
        await _run_all(args, config)


async def _run_all(args: argparse.Namespace, config: CoreConfig) -> None:
    client, registry, executor = await build_client(config)
    llm = _llm_for(config)
    prompt = PromptAssembler(
        rules_text=Path("prompts/rules.md").read_text(), persona_text=Path("prompts/persona.md").read_text(),
        registry=registry,
    )
    players = cast("PlayersService", executor.services["players"])
    player = await players.create_player()
    tags = all_manifest_tags(registry)
    print(f"모델 {config.llm_provider}/{config.agent_model} · 모드 {config.skill_loading_mode}")

    jobs: list[tuple[str, str, str, EvalCase | Scenario]] = []
    if args.suite in ("all", "examples"):
        jobs += [("examples", c.skill_name, c.question, c) for c in collect_eval_cases(registry)]
    if args.suite in ("all", "scenarios"):
        jobs += [("scenarios", s.name, s.question, s) for s in SCENARIOS]
    if args.only:
        jobs = [job for job in jobs if args.only in job[1] or args.only in job[2]]

    reports: dict[str, EvalReport] = {"examples": EvalReport(), "scenarios": EvalReport()}
    try:
        for suite, label, question, case in jobs:
            for attempt in range(args.repeat):
                result, trace, elapsed = await _run_turn(
                    config=config, llm=llm, prompt=prompt, executor=executor, client=client, registry=registry,
                    player_id=player.id, tags=tags, question=question,
                )
                report = reports[suite]
                report.total += 1
                report.latencies_seconds.append(elapsed)
                report.estimated_cost_usd += estimate_cost_usd(
                    config.agent_model, input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens, cached_input_tokens=result.usage.cached_input_tokens,
                )
                problems = judge_example(case, trace) if isinstance(case, EvalCase) else judge_scenario(case, trace)
                if assemble_response(result).numeric_violation:
                    report.numeric_violations += 1
                    problems.append("숫자 규칙 위반")
                if problems:
                    report.failures += 1
                tag = "통과" if not problems else "실패"
                run = f" #{attempt + 1}" if args.repeat > 1 else ""
                print(f"[{tag}] {suite}/{label}{run} {elapsed:4.1f}s {question!r}", flush=True)
                print(f"        도구 {trace.tool_names}  보인 표 {[(t, sorted(trace.lineage.get(t, set()))) for t in trace.shown_table_ids]}")
                for problem in problems:
                    print(f"        - {problem}")
                await asyncio.sleep(1)
    finally:
        await llm.aclose()
        await client.close()

    for suite, report in reports.items():
        if not report.total:
            continue
        avg = sum(report.latencies_seconds) / len(report.latencies_seconds)
        print(
            f"[{suite}] 총 {report.total}건 | 실패 {report.failures} | 숫자 규칙 위반 {report.numeric_violations} | "
            f"평균 지연 {avg:.2f}s | 추정 비용 ${report.estimated_cost_usd:.4f}"
        )


if __name__ == "__main__":
    asyncio.run(_main())
