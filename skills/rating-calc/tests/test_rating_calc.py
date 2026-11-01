# skills/rating-calc/tests/test_rating_calc.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from core.tool_types import ToolContext
from services.chunithm.profile import CHUNITHM
from services.game.profile import GameProfile
from services.game.terms import TermsService
from services.maimai.profile import MAIMAI

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_rating_calc_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx(profile: GameProfile) -> ToolContext:
    terms = TermsService(profile.terms_path, game=profile.game, percent_scores=profile.score_is_percent)
    return ToolContext(player_id="p1", tags=frozenset(), services={"terms": terms}, game=profile.game)


@pytest.fixture
def skill() -> ModuleType:
    return _load_skill()


# --- song_rating -----------------------------------------------------------------------------------------------

async def test_maimai_song_rating_at_sss_plus_boundary(skill: ModuleType) -> None:
    result = await skill.song_rating(_ctx(MAIMAI), const=12.5, score=1_005_000)

    assert result.error is None and result.data is not None
    assert result.data["song_rating"] == "281"  # floor(12.5 × 1.005 × 22.4)
    assert result.data["rank"] == "SSS+" and result.data["score_text"] == "100.5000%"


async def test_chunithm_song_rating_has_two_places_and_names_the_rank(skill: ModuleType) -> None:
    result = await skill.song_rating(_ctx(CHUNITHM), const=15.4, score=1_009_000)

    assert result.data is not None
    assert result.data["song_rating"] == "17.55"  # SSS+(1,009,000점)는 상수 + 2.15
    assert result.data["rank"] == "SSS+" and result.data["score_text"] == "1,009,000"


async def test_song_rating_rejects_an_out_of_range_const(skill: ModuleType) -> None:
    result = await skill.song_rating(_ctx(CHUNITHM), const=99.0, score=1_000_000)

    assert result.error is not None


# --- find_const ------------------------------------------------------------------------------------------------

async def test_maimai_has_no_const_giving_exactly_280_at_sss_plus(skill: ModuleType) -> None:
    """2026-11-01 사용자 질문 "마이에서 상수 몇인 채보를 SSS+ 딱 했을 때 280점이야?" — 12.4는 279, 12.5는 281이다."""
    result = await skill.find_const(_ctx(MAIMAI), rating=280, score=1_005_000)

    assert result.data is not None
    assert result.data["consts"] == [] and result.data["match_count"] == 0
    assert result.data["below"] == {"const": "12.4", "song_rating": "279"}
    assert result.data["above"] == {"const": "12.5", "song_rating": "281"}
    assert result.display is not None and "없어" in result.display.content


async def test_find_const_returns_the_exact_const(skill: ModuleType) -> None:
    result = await skill.find_const(_ctx(MAIMAI), rating=281, score=1_005_000)

    assert result.data is not None and result.data["consts"] == ["12.5"]


async def test_chunithm_find_const_inverts_song_rating(skill: ModuleType) -> None:
    result = await skill.find_const(_ctx(CHUNITHM), rating=17.55, score=1_009_000)

    assert result.data is not None and result.data["consts"] == ["15.4"]


async def test_find_const_caps_the_listed_matches(skill: ModuleType) -> None:
    result = await skill.find_const(_ctx(CHUNITHM), rating=0, score=0)  # 점수 0이면 어느 상수든 곡 레이팅 0

    assert result.data is not None
    assert len(result.data["consts"]) == 10 and result.data["match_count"] > 10


# --- needed_score ----------------------------------------------------------------------------------------------

async def test_chunithm_needed_score_is_the_lowest_score_reaching_the_target(skill: ModuleType) -> None:
    result = await skill.needed_score(_ctx(CHUNITHM), const=15.4, rating=17.40)

    assert result.data is not None and result.data["reachable"] is True
    needed = result.data["needed_score"]
    assert needed == 1_007_500 and result.data["rank"] == "SSS"
    before, at = CHUNITHM.chart_rating(needed - 1, 15.4), CHUNITHM.chart_rating(needed, 15.4)
    assert before is not None and at is not None and before < at  # 한 점 모자라면 목표에 못 닿는다


async def test_maimai_needed_score_reports_percent_and_rank(skill: ModuleType) -> None:
    result = await skill.needed_score(_ctx(MAIMAI), const=12.5, rating=281)

    assert result.data is not None
    assert result.data["needed_score"] == 1_005_000
    assert result.data["score_text"] == "100.5000%" and result.data["rank"] == "SSS+"


async def test_needed_score_says_unreachable_when_even_a_perfect_score_falls_short(skill: ModuleType) -> None:
    result = await skill.needed_score(_ctx(MAIMAI), const=12.5, rating=300)

    assert result.error is None and result.data is not None
    assert result.data["reachable"] is False and result.data["max_song_rating"] == "281"
