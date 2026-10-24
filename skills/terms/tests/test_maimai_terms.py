# skills/terms/tests/test_maimai_terms.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from core.tool_types import ToolContext
from services.game.terms import TermsService
from services.maimai.profile import MAIMAI

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_maimai_terms_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx(game: str) -> ToolContext:
    if game == "maimai":
        terms = TermsService(MAIMAI.terms_path, game="maimai", percent_scores=True)
    else:
        terms = TermsService()
    return ToolContext(player_id="p1", tags=frozenset(), services={"terms": terms}, game=game)


async def test_explain_shows_the_rank_value_in_the_games_own_format() -> None:
    skill = _load_skill()

    chunithm = await skill.explain(_ctx("chunithm"), term="SSS")
    maimai = await skill.explain(_ctx("maimai"), term="SSS")

    assert chunithm.display is not None and "값: 1,007,500" in (chunithm.display.content or "")
    assert maimai.display is not None and "값: 100.0000%" in (maimai.display.content or "")
    assert maimai.data["value"] == 1_000_000


async def test_maimai_has_no_concept_terms_so_chunithm_slang_is_unknown_there() -> None:
    skill = _load_skill()

    result = await skill.explain(_ctx("maimai"), term="오버파워")

    assert result.error is not None and "모르는 용어" in result.error
