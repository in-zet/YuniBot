from core.core_tools import CORE_TOOL_SPECS, list_capabilities, open_skill, remember
from core.skill_loading import SkillLoadState
from core.tool_types import ToolContext


def test_core_tool_specs_contains_all_three() -> None:
    names = {spec.name for spec in CORE_TOOL_SPECS}
    assert names == {"open_skill", "remember", "list_capabilities"}


def test_list_capabilities_importable_without_core_capabilities_module() -> None:
    # list_capabilities는 core/capabilities.py를 함수 안에서 늦게 import한다. 이 모듈을
    # import하는 것만으로는(= 이 함수를 직접 부르지 않는 한) 깨지지 않아야 한다.
    assert list_capabilities.__name__ == "list_capabilities"


async def test_open_skill_marks_skill_open_in_state() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border"})
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={"_skill_load_state": state})
    result = await open_skill(ctx, skill_name="chunithm-border")
    assert result.error is None
    assert state.level_for("chunithm-border") == "full"


async def test_open_skill_rejects_unknown_skill() -> None:
    state = SkillLoadState(mode="instructions", known_skills={"chunithm-border"})
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={"_skill_load_state": state})
    result = await open_skill(ctx, skill_name="does-not-exist")
    assert result.error is not None


async def test_remember_appends_to_memory(monkeypatch) -> None:
    saved: dict[str, str] = {}

    class _FakeMemory:
        async def get(self, player_id: str) -> str:
            return saved.get(player_id, "")

        async def set(self, player_id: str, content: str) -> None:
            saved[player_id] = content

    ctx = ToolContext(player_id="p1", tags=frozenset(), services={"memory": _FakeMemory()})
    result = await remember(ctx, content="호칭은 유니상")
    assert result.error is None
    assert "호칭은 유니상" in saved["p1"]
