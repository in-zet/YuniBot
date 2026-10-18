"""테스트에서 임시 디렉터리에 스킬 폴더(SKILL.md + skill.py)를 바로 만들어 쓰기 위한 도우미.

슬래시 경로 충돌처럼 조합이 많은 경우를 픽스처 폴더로 하나하나 두지 않고, 테스트 안에서
필요한 스킬을 그 자리에서 만든다.
"""

from __future__ import annotations

import json
from pathlib import Path

_MANIFEST = """---
name: {name}
description: 테스트용 임시 스킬
examples:
  - 예시
category: 메모
game: common
version: 1
services: []
{weight}---

테스트용.
"""

_HEADER = """from core.decorators import slash_only, tool
from core.tool_types import ToolContext, ToolResult

_ = (slash_only, tool)

"""


def write_skill(
    root: Path, name: str, body: str, *, manifest_name: str | None = None, weight: str | None = None
) -> Path:
    """root/<name>/ 아래에 스킬을 만든다. body는 skill.py의 import 뒤에 붙는 코드다.

    manifest_name을 주면 폴더 이름 대신 그 값을 manifest의 name으로 쓴다(잘못된 이름 시험용).
    weight를 주면("heavy") manifest에 그 줄을 추가한다(기본은 생략 — manifest의 기본값 light).
    """
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    manifest_value = json.dumps(manifest_name if manifest_name is not None else name)  # YAML 큰따옴표 문자열
    weight_line = f"weight: {weight}\n" if weight is not None else ""
    (skill_dir / "SKILL.md").write_text(_MANIFEST.format(name=manifest_value, weight=weight_line))
    (skill_dir / "skill.py").write_text(_HEADER + body)
    return skill_dir


def slash_tool(fn_name: str, slash: str, *, args: str = "") -> str:
    """slash 경로 하나를 가진 단순한 @tool 함수 소스를 만든다."""
    extra = f", {args}" if args else ""
    return (
        f"@tool(slash={slash!r})\n"
        f"async def {fn_name}(ctx: ToolContext{extra}) -> ToolResult:\n"
        f'    """테스트용 도구."""\n'
        f"    return ToolResult(data={{}})\n\n\n"
    )
