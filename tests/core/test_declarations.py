
from core.declarations import (
    build_tool_declaration,
    gemini_tool_name,
    tool_name_problems,
)
from core.decorators import tool
from core.tool_types import ChartRef, RankRef, ToolContext, ToolResult


@tool()
async def _example(ctx: ToolContext, chart: ChartRef, rank: RankRef | None = None, count: int = 1) -> ToolResult:
    """예시 도구. 표준 타입과 기본값 있는 int를 섞어 쓴다."""
    return ToolResult()


def test_gemini_tool_name_keeps_hyphen() -> None:
    # 00-core.md 미확인 사항: 하이픈은 Gemini 함수 이름에서 그대로 허용된다(변환 불필요).
    assert gemini_tool_name("chunithm-border", "lookup") == "chunithm-border_lookup"


def test_gemini_tool_name_without_skill_prefix() -> None:
    assert gemini_tool_name(None, "open_skill") == "open_skill"


def test_tool_name_problems_rejects_invalid_chars() -> None:
    assert tool_name_problems("bad name!") != []
    assert tool_name_problems("chunithm-border_lookup") == []


def test_tool_name_problems_rejects_too_long() -> None:
    assert tool_name_problems("a" * 129) != []
    assert tool_name_problems("a" * 128) == []


def test_build_tool_declaration_maps_ref_types_and_defaults() -> None:
    decl = build_tool_declaration(_example.__yuni_tool__, skill_name="chunithm-border")  # pyright: ignore[reportFunctionMemberAccess]

    assert decl.name == "chunithm-border__example"
    assert decl.description.startswith("예시 도구")
    schema = decl.parameters_json_schema
    assert schema["type"] == "object"
    assert schema["properties"]["chart"] == {"type": "string"}
    assert schema["properties"]["rank"] == {"type": "string"}
    assert schema["properties"]["count"] == {"type": "integer"}
    assert schema["required"] == ["chart"]  # rank/count는 기본값이 있어 required가 아니다
