# core/capabilities.py
from __future__ import annotations

from dataclasses import dataclass, field

from core.decorators import SlashOnlySpec, ToolSpec
from core.manifest import DefinedTag
from core.permissions import TagRequirement, check_tags
from core.registry import LoadedSkill, SkillRegistry
from core.tool_types import ToolContext, ToolResult

# 못 하는 일(00-core.md "할 수 있는 일 안내"). 도움말과 list_capabilities가 같은 목록을 쓴다.
NOT_SUPPORTED: tuple[str, ...] = (
    "일본판 계정 동기화",
    "멘션 없는 대화에 끼어들기",
    "꺼져 있는 새벽 시간의 응답",
    "말만 듣고 디스코드 계정 연결하기",
    "한 플레이어가 CHUNITHM-NET 계정 여러 개 쓰기",
)
NOT_SUPPORTED_TEXT = "아직 못 하는 일도 있어: " + ", ".join(NOT_SUPPORTED) + "."

# 분류마다 한 줄. 분류 목록은 core/manifest.py의 VALID_CATEGORIES와 같아야 한다(시험이 확인한다).
CATEGORY_BLURBS: dict[str, str] = {
    "계정 연결": "CHUNITHM-NET 로그인, 기록 공개 설정, 디코 부계정 연결",
    "곡 정보": "곡 상수, 노트 수, 채보 링크, 랭크 보더",
    "기록과 리포트": "레이팅, 베스트, 최근 플레이, 동기화, 표 그림",
    "목표": "레이팅이나 곡별 목표와 기한, 진행률",
    "세션과 후기": "지난 플레이 세션 다시 보기, 후기 남기기",
    "오락실": "오락실별 게임 설정, 인게임 설정과 즐겨찾기",
    "메모": "곡이나 채보에 메모 남기기",
    "알림": "업데이트 일정, 신곡, 알림 켜고 끄기",
    "질문 답변": "츄니즘 용어와 공략 질문",
    "메모리": "내가 기억하는 내용 보기, 지우기",
    "이정표": "함께 넘은 이정표 돌아보기, 오늘 칠 곡 고르기, 약속",
    "관리": "사용량, 서버별 스킬 설정, 태그 (관리자 전용)",
    "폴더": "채보를 폴더로 모아 관리하기",
    "유니 설정": "유니와 나비, Linked VERSE 이야기",
}

_EXAMPLES_PER_SKILL = 2
_COMMANDS_PER_GROUP = 8


@dataclass(slots=True, kw_only=True)
class CapabilityEntry:
    name: str
    summary: str
    examples: list[str]
    commands: list[str]


@dataclass(slots=True, kw_only=True)
class Capabilities:
    by_category: dict[str, list[CapabilityEntry]] = field(default_factory=dict)
    locked_hints: list[str] = field(default_factory=list)


def _usable_commands(skill: LoadedSkill, player_tags: set[str]) -> list[str]:
    """이 플레이어가 지금 쓸 수 있는 슬래시 명령 경로(스킬 태그는 이미 통과했다)."""
    found: list[tuple[int, str]] = []
    for spec in [*skill.code.tools, *skill.code.slash_only_tools]:
        if not isinstance(spec, (ToolSpec, SlashOnlySpec)) or not spec.slash:
            continue
        if not check_tags(TagRequirement(tags=list(spec.tags), tags_any=list(spec.tags_any)), player_tags):
            continue
        # 데코레이터가 모으는 순서는 보장되지 않아, 스킬 파일에 적힌 순서(= 쓰는 순서)로 세운다.
        code = getattr(spec.fn, "__code__", None)
        found.append((code.co_firstlineno if code is not None else 0, spec.slash))
    return [slash for _, slash in sorted(found)]


def format_commands(commands: list[str]) -> str:
    """`/폴더 생성`, `/폴더 삭제`처럼 같은 묶음은 `/폴더 생성·삭제`로 합쳐 짧게 보인다."""
    groups: dict[str, list[str]] = {}
    for command in commands:
        head, _, rest = command.partition(" ")
        groups.setdefault(head, [])
        if rest:
            groups[head].append(rest)
    parts: list[str] = []
    for head, subs in groups.items():
        if not subs:
            parts.append(f"`/{head}`")
        else:
            shown = "·".join(subs[:_COMMANDS_PER_GROUP])
            more = f" 외 {len(subs) - _COMMANDS_PER_GROUP}개" if len(subs) > _COMMANDS_PER_GROUP else ""
            parts.append(f"`/{head} {shown}`{more}")
    return " ".join(parts)


def collect_capabilities(
    *, registry: SkillRegistry, player_tags: set[str], category: str | None
) -> Capabilities:
    """등록된 스킬 카드에서 이 플레이어에게 보일 것만 모은다. 부여 태그가 없는 스킬은 완전히 빠진다
    (00-core.md "권한 태그"). 상태 태그가 모자라 잠긴 것은 얻는 방법만 안내한다."""
    result = Capabilities()
    for skill in registry.enabled_skills():
        manifest = skill.manifest
        requirement = TagRequirement(tags=list(manifest.tags), tags_any=list(manifest.tags_any))
        if not check_tags(requirement, player_tags):
            continue
        if category is not None and manifest.category != category:
            continue
        result.by_category.setdefault(manifest.category, []).append(
            CapabilityEntry(
                name=manifest.name,
                summary=manifest.summary or manifest.description,
                examples=list(manifest.examples[:_EXAMPLES_PER_SKILL]),
                commands=_usable_commands(skill, player_tags),
            )
        )
        for tag in manifest.defines_tags:
            if tag.name not in player_tags and not tag.hidden and tag.how_to_get not in result.locked_hints:
                result.locked_hints.append(tag.how_to_get)
    return result


def build_capabilities_text(
    *, registry: SkillRegistry, player_tags: set[str], tag_definitions: dict[str, DefinedTag], category: str | None
) -> str:
    """모델에게 주는 안내(list_capabilities의 data). 플레이어에게 보이는 글은 build_help_*가 만든다."""
    found = collect_capabilities(registry=registry, player_tags=player_tags, category=category)
    lines: list[str] = []
    for cat, entries in sorted(found.by_category.items()):
        lines.append(f"## {cat}")
        for entry in entries:
            commands = f" (슬래시: {format_commands(entry.commands)})" if entry.commands else ""
            lines.append(f"- {entry.name}: {entry.summary}{commands}")

    if found.locked_hints and category is None:
        lines.append("## 더 쓸 수 있는 것")
        lines.extend(f"- {hint}" for hint in found.locked_hints)

    if category is None:
        lines.append(NOT_SUPPORTED_TEXT)

    return "\n".join(lines) if lines else "지금 쓸 수 있는 기능이 없어."


HELP_INTRO = (
    "안녕, 나는 유니야! 츄니즘 기록이랑 곡 정보를 같이 챙겨 주는 서포터지~\n"
    "나를 멘션하거나 내 메시지에 답장해서 편하게 말을 걸어 줘. 슬래시 명령(`/`)은 나를 거치지 않고 바로 돼!"
)
_HELP_MENU_GUIDE = "아래 메뉴에서 분류를 고르면 어떻게 말하고 무슨 명령을 쓰는지 알려 줄게."


def help_categories(found: Capabilities) -> list[str]:
    """도움말에 보일 분류 이름(선언된 순서대로, 보일 스킬이 있는 것만)."""
    return [cat for cat in CATEGORY_BLURBS if cat in found.by_category]


def build_help_overview(found: Capabilities) -> str:
    lines = [HELP_INTRO]
    if found.locked_hints:
        lines.append("")
        lines.append("**더 쓰고 싶다면**")
        lines.extend(f"- {hint}" for hint in found.locked_hints)
    lines.append("")
    lines.extend(f"- **{cat}**: {CATEGORY_BLURBS[cat]}" for cat in help_categories(found))
    lines.append("")
    lines.append(_HELP_MENU_GUIDE)
    lines.append(NOT_SUPPORTED_TEXT)
    return "\n".join(lines)


def build_help_category(found: Capabilities, category: str) -> str:
    entries = found.by_category.get(category, [])
    if not entries:
        return "그 분류는 지금 보여 줄 게 없어."
    lines = [f"**{category}**"]
    for entry in entries:
        lines.append(f"- {entry.summary}")
        if entry.examples:
            lines.append("  이렇게 말해 봐: " + " / ".join(f"\"{example}\"" for example in entry.examples))
        if entry.commands:
            lines.append(f"  명령: {format_commands(entry.commands)}")
    return "\n".join(lines)


async def build_capabilities_result(ctx: ToolContext, *, category: str | None) -> ToolResult:
    """core_tools.list_capabilities가 부르는 자리. 실제로는 core/agent.py의 AgentLoop가
    list_capabilities 호출을 가로채 PromptAssembler로 직접 처리하므로,
    이 함수는 AgentLoop를 거치지 않는 경로(예: 향후 직접 단위 테스트)를 위한 대비용 구현이고
    평소 에이전트 루프에서는 쓰이지 않는다. 레지스트리 접근이 없는 상태에서는 안내만 못 한다고
    답한다."""
    return ToolResult(
        error="지금 경로에서는 할 수 있는 일을 바로 보여 줄 수 없어. 멘션으로 다시 물어봐 줘.",
    )
