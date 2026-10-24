# core/declarations.py
from __future__ import annotations

import enum
import re
from typing import get_args, get_type_hints, is_typeddict

from core.decorators import ToolSpec
from core.llm import ToolDeclaration
from core.schema import ArgSpec, build_arg_schema, is_list_type
from core.tool_types import STANDARD_REF_TYPES, GameRef

# ai.google.dev/api/generate-content의 FunctionDeclaration.name 설명 원문:
# "Must be a-z, A-Z, 0-9, or contain underscores, colons, dots, and dashes,
# with a maximum length of 128." (2026-10-12 확인)
_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def gemini_tool_name(skill_name: str | None, fn_name: str) -> str:
    """스킬 소속 도구는 '<스킬>_<함수>', 코어 전용 도구는 함수 이름 그대로.

    스킬 이름의 하이픈(chunithm-border 등)은 Gemini 함수 이름에서 그대로 허용되므로
    밑줄로 바꾸지 않는다(00-core.md 미확인 사항 해소, 2026-10-12 공식 문서 확인).
    """
    return fn_name if skill_name is None else f"{skill_name}_{fn_name}"


def tool_name_problems(name: str) -> list[str]:
    if not _NAME_RE.fullmatch(name):
        return [f"'{name}'은 Gemini 함수 이름 규칙(a-z,A-Z,0-9,_,.,:,- 128자 이내)을 어긴다"]
    return []


def _json_type(py_type: type) -> str:
    if py_type is int:
        return "integer"
    if py_type is float:
        return "number"
    if py_type is bool:
        return "boolean"
    return "string"  # str, 표준 인자 타입(원문 그대로 받음), enum(이름을 문자열로 받음)


def _item_schema(item: type) -> dict[str, object]:
    if is_typeddict(item):
        hints = get_type_hints(item)
        return {
            "type": "object",
            "properties": {name: {"type": _json_type(py_type)} for name, py_type in hints.items()},
            "required": list(hints),
        }
    return {"type": _json_type(item)}


def _property_schema(arg: ArgSpec) -> dict[str, object]:
    if is_list_type(arg.py_type):
        (item,) = get_args(arg.py_type)
        return {"type": "array", "items": _item_schema(item)}
    schema: dict[str, object] = {"type": _json_type(arg.py_type)}
    if arg.py_type is GameRef:
        schema["description"] = "사용자가 말한 게임 이름 그대로(예: 마이마이, 츄니즘). 게임을 말하지 않았으면 이 인자를 넣지 않는다."
    if isinstance(arg.py_type, type) and issubclass(arg.py_type, enum.Enum) and arg.py_type not in STANDARD_REF_TYPES:
        schema["enum"] = [member.value for member in arg.py_type]
    return schema


def build_tool_declaration(spec: ToolSpec, *, skill_name: str | None) -> ToolDeclaration:
    """ToolSpec(스킬 도구든 core/core_tools.py의 코어 도구든)을 Gemini 함수 선언으로 바꾼다."""
    name = gemini_tool_name(skill_name, spec.name)
    problems = tool_name_problems(name)
    if problems:
        raise ValueError("; ".join(problems))

    schema = build_arg_schema(spec.fn)
    properties = {arg.name: _property_schema(arg) for arg in schema.args}
    required = [arg.name for arg in schema.args if arg.required]

    description = (spec.fn.__doc__ or "").strip()[:1024]
    return ToolDeclaration(
        name=name,
        description=description,
        parameters_json_schema={"type": "object", "properties": properties, "required": required},
    )
