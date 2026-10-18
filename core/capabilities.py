# core/capabilities.py
from __future__ import annotations

from core.manifest import DefinedTag
from core.permissions import TagRequirement, check_tags
from core.registry import SkillRegistry
from core.tool_types import ToolContext, ToolResult

_NOT_SUPPORTED = (
    "일본판 계정 동기화, 멘션 없는 대화에 끼어들기, 꺼져 있는 새벽 시간의 응답, "
    "말만 듣고 디스코드 계정 연결하기, 한 플레이어가 CHUNITHM-NET 계정 여러 개 쓰기는 못 한다."
)


def build_capabilities_text(
    *, registry: SkillRegistry, player_tags: set[str], tag_definitions: dict[str, DefinedTag], category: str | None
) -> str:
    by_category: dict[str, list[str]] = {}
    locked_hints: list[str] = []

    for skill in registry.enabled_skills():
        manifest = skill.manifest
        requirement = TagRequirement(tags=list(manifest.tags), tags_any=list(manifest.tags_any))
        if not check_tags(requirement, player_tags):
            continue  # 부여 태그가 없는 스킬은 카드에서 완전히 빠진다(00-core.md "권한 태그")
        if category is not None and manifest.category != category:
            continue
        by_category.setdefault(manifest.category, []).append(f"{manifest.name}: {manifest.description}")

        for tag in manifest.defines_tags:
            if tag.name not in player_tags and not tag.hidden:
                locked_hints.append(f"{manifest.name} 더 쓰려면: {tag.description} ({tag.how_to_get})")

    lines: list[str] = []
    for cat, entries in sorted(by_category.items()):
        lines.append(f"## {cat}")
        lines.extend(f"- {entry}" for entry in entries)

    if locked_hints and category is None:
        lines.append("## 더 쓸 수 있는 것")
        lines.extend(f"- {hint}" for hint in locked_hints)

    if category is None:
        lines.append(_NOT_SUPPORTED)

    return "\n".join(lines) if lines else "지금 쓸 수 있는 기능이 없다."


async def build_capabilities_result(ctx: ToolContext, *, category: str | None) -> ToolResult:
    """core_tools.list_capabilities가 부르는 자리. 실제로는 core/agent.py의 AgentLoop가
    list_capabilities 호출을 가로채 PromptAssembler로 직접 처리하므로,
    이 함수는 AgentLoop를 거치지 않는 경로(예: 향후 직접 단위 테스트)를 위한 대비용 구현이고
    평소 에이전트 루프에서는 쓰이지 않는다. 레지스트리 접근이 없는 상태에서는 안내만 못 한다고
    답한다."""
    return ToolResult(
        error="지금 경로에서는 할 수 있는 일을 바로 보여줄 수 없다. 멘션으로 다시 물어봐 달라.",
    )
