"""도구 선택 정확도와 숫자 규칙 위반을 재는 평가 명령. 실제 Gemini API를 부르므로
GEMINI_API_KEY가 필요하다. uv run pytest에는 수집되지 않는다.

사용법:
    uv run python3 scripts/eval_agent.py --compare-models gemini-3.8-flash,gemini-3.5-flash-lite
    uv run python3 scripts/eval_agent.py --compare-modes --model gemini-3.8-flash
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from google.genai.errors import ServerError

from core.agent import AgentLoop, AgentTurnResult
from core.executor import ToolExecutor
from core.llm_gemini import GeminiLlmAdapter
from core.llm_pricing import estimate_cost_usd
from core.permissions import InMemoryTagProvider
from core.prompt import PromptAssembler
from core.registry import SkillRegistry
from core.resolvers import ResolverRegistry
from core.response_assembly import assemble_response
from core.skill_loading import SkillLoadState
from core.tool_types import ToolContext


@dataclass(slots=True, kw_only=True, frozen=True)
class EvalCase:
    skill_name: str
    question: str


@dataclass(slots=True, kw_only=True)
class EvalReport:
    total: int = 0
    tool_choice_errors: int = 0
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


def score_tool_choice(*, expected_skill: str, first_tool_name: str, mode: Literal["instructions", "full"]) -> bool:
    if mode == "full":
        return first_tool_name == "open_skill"
    if first_tool_name in ("open_skill", "remember", "list_capabilities"):
        return False
    return first_tool_name.startswith(f"{expected_skill}_")


class _RecordingAgentLoop(AgentLoop):
    """첫 실제 도구 호출 이름을 기록하려고 _invoke를 가로챈다(평가 전용, core/agent.py는 안 바꾼다)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.first_tool_name: str | None = None

    async def _invoke(self, ctx: ToolContext, tool_name: str, args: dict[str, object]):  # type: ignore[override]
        if self.first_tool_name is None:
            self.first_tool_name = tool_name
        return await super()._invoke(ctx, tool_name, args)


async def _run_with_retry(
    loop: AgentLoop, *, ctx: ToolContext, state: SkillLoadState, question: str
) -> AgentTurnResult:
    """Gemini가 "현재 수요가 많다"는 503을 던진다(실제로 관찰됨, 분당 요청 수 제한에 걸리는
    것으로 보인다 — 짧은 재시도로는 안 풀려서 간격을 늘렸다). 평가 케이스 하나가 이걸로
    전체 비교를 멈춰 세우지 않도록 재시도한다."""
    for attempt in range(4):
        try:
            return await loop.run(ctx=ctx, skill_state=state, user_text=question, prompt_context_snippets=[])
        except ServerError:
            if attempt == 3:
                raise
            await asyncio.sleep(20 * (attempt + 1))
    raise AssertionError("unreachable")


async def run_eval(
    *, model: str, mode: Literal["instructions", "full"], registry: SkillRegistry, executor: ToolExecutor, api_key: str
) -> EvalReport:
    report = EvalReport()
    llm = GeminiLlmAdapter(api_key=api_key)
    prompt = PromptAssembler(
        rules_text=Path("prompts/rules.md").read_text(), persona_text=Path("prompts/persona.md").read_text(),
        registry=registry,
    )
    known_skills = {s.manifest.name for s in registry.enabled_skills()}
    eval_tags = all_manifest_tags(registry)

    for case in collect_eval_cases(registry):
        loop = _RecordingAgentLoop(
            llm=llm, executor=executor, prompt=prompt, model=model, thinking_level="low",
            max_tool_calls=5, max_tokens=50000,
        )
        ctx = ToolContext(player_id="eval", tags=eval_tags, services={})
        state = SkillLoadState(mode=mode, known_skills=known_skills)
        started = time.monotonic()
        result = await _run_with_retry(loop, ctx=ctx, state=state, question=case.question)
        report.latencies_seconds.append(time.monotonic() - started)
        report.total += 1
        report.estimated_cost_usd += estimate_cost_usd(
            model, input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens
        )
        if loop.first_tool_name is None or not score_tool_choice(
            expected_skill=case.skill_name, first_tool_name=loop.first_tool_name, mode=mode
        ):
            report.tool_choice_errors += 1
            print(
                f"  [오류] 기대 스킬={case.skill_name!r} 질문={case.question!r} "
                f"실제 첫 도구={loop.first_tool_name!r}"
            )
        if assemble_response(result).numeric_violation:
            report.numeric_violations += 1
        await asyncio.sleep(2)  # 분당 요청 수 제한을 피하려고 케이스 사이에 간격을 둔다

    await llm.aclose()
    return report


def _print_report(label: str, report: EvalReport) -> None:
    avg_latency = sum(report.latencies_seconds) / len(report.latencies_seconds) if report.latencies_seconds else 0.0
    print(f"[{label}] 총 {report.total}건 | 도구 선택 오류 {report.tool_choice_errors} | "
          f"숫자 규칙 위반 {report.numeric_violations} | 평균 지연 {avg_latency:.2f}s | "
          f"추정 비용 ${report.estimated_cost_usd:.4f}")


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemini-3.8-flash")
    parser.add_argument("--mode", default="instructions", choices=["instructions", "full"])
    parser.add_argument("--compare-models")
    parser.add_argument("--compare-modes", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY가 없다")

    registry = SkillRegistry([Path("skills")])
    registry.load()
    executor = ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions=registry.tag_definitions())

    runs: list[tuple[str, Literal["instructions", "full"]]] = []
    if args.compare_models:
        runs = [(m.strip(), args.mode) for m in args.compare_models.split(",")]
    elif args.compare_modes:
        runs = [(args.model, "instructions"), (args.model, "full")]
    else:
        runs = [(args.model, args.mode)]

    for model, mode in runs:
        report = await run_eval(model=model, mode=mode, registry=registry, executor=executor, api_key=api_key)
        _print_report(f"{model}/{mode}", report)


if __name__ == "__main__":
    asyncio.run(_main())
