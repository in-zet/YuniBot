# skills/yuni-lore/skill.py
from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from core.decorators import tool
from core.tool_types import ToolContext, ToolResult

_LORE_PATH = Path(__file__).with_name("lore.md")
_HEADING = re.compile(r"^## \[(?P<id>[a-z0-9_]+)\] (?P<title>.+)$", re.MULTILINE)
_SPOILER_MARK = "(스포일러)"


class _Section(NamedTuple):
    id: str
    title: str
    spoiler: bool
    text: str


def _parse(raw: str) -> list[_Section]:
    """`## [id] 제목` 단위로 자른다. 첫 제목 앞의 머리말은 버린다. 제목 끝의 (스포일러)가 스포일러 표시다."""
    matches = list(_HEADING.finditer(raw))
    sections: list[_Section] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        title = match["title"].strip()
        spoiler = title.endswith(_SPOILER_MARK)
        if spoiler:
            title = title.removesuffix(_SPOILER_MARK).strip()
        sections.append(_Section(match["id"], title, spoiler, raw[match.end():end].strip()))
    return sections


def _load() -> list[_Section] | None:
    try:
        return _parse(_LORE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


@tool()
async def read_lore(ctx: ToolContext, section: str | None = None, spoiler_ok: bool = False) -> ToolResult:
    """유니와 나비, Linked VERSE 이야기의 설정집을 구간 하나씩 읽는다. section을 비우면 구간 목록(id, 제목,
    스포일러 여부)만 돌려준다. 스포일러 구간은 플레이어가 스포일러를 봐도 된다고 답한 뒤에만 spoiler_ok를 켜서 읽는다."""
    sections = _load()
    if sections is None:
        return ToolResult(error="설정집 파일을 찾을 수 없어")

    if section is None:
        return ToolResult(data={
            "sections": [{"id": s.id, "title": s.title, "spoiler": s.spoiler} for s in sections],
        })

    found = next((s for s in sections if s.id == section), None)
    if found is None:
        return ToolResult(error=f"'{section}'이라는 구간은 없다. 구간 id: {', '.join(s.id for s in sections)}")
    if found.spoiler and not spoiler_ok:
        return ToolResult(
            error="결말 쪽 스포일러 구간이다. 먼저 플레이어에게 스포일러를 봐도 되는지 묻고, 괜찮다고 하면 spoiler_ok를 켜서 다시 불러라"
        )
    return ToolResult(data={"id": found.id, "title": found.title, "spoiler": found.spoiler, "text": found.text})
