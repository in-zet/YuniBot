"""스킬 테스트 자리(00-core.md: tests/는 필수). 복사한 뒤 파일 이름을 test_<스킬>.py로 바꾼다.

스킬 폴더 이름에 하이픈이 들어가 import 경로로 쓸 수 없으므로 skill.py를 파일 경로로 불러온다.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from core.tool_types import ToolContext

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_template_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_example_returns_name() -> None:
    skill = _load_skill()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    result = await skill.example(ctx, name="유니")

    assert result.error is None
    assert result.data == {"name": "유니"}
