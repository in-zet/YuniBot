# tests/services/arcaea/test_rating.py
"""Arcaea 포텐셜(7.0.0). 실제 Arcaea Online 표본(`docs/reference/arcaea-online/`)으로 검산한다."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from core.tool_types import ToolContext
from services.arcaea.profile import ARCAEA
from services.arcaea.rating import chart_rating
from services.game.records_calc import overall_rating
from services.game.terms import TermsService

_SAMPLE = Path("docs/reference/arcaea-online/score_rating_me.json")


@pytest.mark.parametrize(
    ("score", "const", "expected"),
    [
        (10_000_930, 9.3, "11.5000"),  # 10,000,000 이상은 +2, 클리어 +0.2
        (9_937_688, 9.8, "11.6884"),  # 표본 Got hive of Ra FTR: Arcaea Online 11.68844
        (9_748_132, 9.4, "10.4271"),  # 9,800,000 아래: (점수 − 9.5M) ÷ 300,000
        (9_000_000, 1.0, "0.2000"),  # 0 밑으로는 안 내려가고 클리어 보너스만 남는다
    ],
)
def test_chart_rating_follows_the_7_0_formula(score: int, const: float, expected: str) -> None:
    assert chart_rating(score, const) == Decimal(expected)


def test_unknown_const_cannot_be_rated() -> None:
    assert chart_rating(9_900_000, None) is None


def test_track_lost_loses_the_clear_bonus() -> None:
    assert ARCAEA.song_rating(9_937_688, 9.8, "NONE", clear_lamp="TRACK LOST") == Decimal("11.4884")
    assert ARCAEA.song_rating(9_937_688, 9.8, "NONE", clear_lamp="HARD CLEAR") == Decimal("11.6884")


def test_overall_potential_matches_arcaea_online_with_top_10_counted_twice() -> None:
    """표본 계정의 프로필 포텐셜은 11366(=11.366)이었다. 곡별 값은 Arcaea Online이 준 그대로 쓴다."""
    best = json.loads(_SAMPLE.read_text())["value"]["best_rated_scores"]
    ratings = [Decimal(str(entry["rating"])) for entry in best]

    overall = overall_rating(
        ratings, [], best_size=ARCAEA.best_frame_size, new_size=ARCAEA.new_frame_size,
        divisor=ARCAEA.rating_divisor, places=ARCAEA.overall_places, double_top=ARCAEA.best_double_count,
    )

    assert overall == Decimal("11.366")


def test_double_top_counts_the_highest_ones_whatever_the_order() -> None:
    ratings = [Decimal(1), Decimal(3), Decimal(2)]
    assert overall_rating(ratings, [], best_size=3, new_size=0, divisor=4, places=2, double_top=1) == Decimal("2.25")
    assert overall_rating([Decimal(1), None], [], best_size=2, new_size=0, divisor=3, double_top=1) is None


# ---------------------------------------------------------------- 용어


@pytest.fixture
def terms() -> TermsService:
    return TermsService(ARCAEA.terms_path, game="arcaea", percent_scores=ARCAEA.score_is_percent)


_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})


async def test_ranks_and_their_slang(terms: TermsService) -> None:
    assert (await terms.resolve_rank("익플", _CTX)).min_score == 9_900_000
    assert (await terms.resolve_rank("EX", _CTX)).min_score == 9_800_000
    scores = list(terms.rank_scores().values())
    assert scores == sorted(scores, reverse=True) and len(scores) == 7


async def test_difficulties_and_lamps_cover_the_profile(terms: TermsService) -> None:
    for long_name, code in ARCAEA.long_to_short.items():
        assert (await terms.resolve_difficulty(code, _CTX)).canonical == long_name
    assert (await terms.resolve_difficulty("퓨처", _CTX)).canonical == "FUTURE"
    for name in (*ARCAEA.clear_lamp_order, *ARCAEA.combo_lamp_order):
        if name != "NONE":
            assert (await terms.resolve_lamp(name, _CTX)).canonical == name
    assert (await terms.resolve_lamp("퓨메", _CTX)).min_score == 10_000_000
