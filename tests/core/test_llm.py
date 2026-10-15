# tests/core/test_llm.py
from core.llm import (
    FunctionCallRequest,
    LlmContent,
    LlmPart,
    ToolDeclaration,
    UsageInfo,
)


def test_llm_content_round_trips_parts() -> None:
    content = LlmContent(
        role="model",
        parts=[LlmPart(text=None, function_call=FunctionCallRequest(call_id="1", name="x_y", args={"a": 1}))],
    )
    assert content.role == "model"
    assert content.parts[0].function_call is not None
    assert content.parts[0].function_call.name == "x_y"


def test_tool_declaration_is_plain_data() -> None:
    decl = ToolDeclaration(name="chunithm-border_lookup", description="desc", parameters_json_schema={"type": "object"})
    assert decl.name == "chunithm-border_lookup"


def test_usage_info_fields() -> None:
    usage = UsageInfo(input_tokens=10, output_tokens=5)
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
