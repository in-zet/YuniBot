# skills/rating-calc/skill.py
from __future__ import annotations

from decimal import Decimal

from core.decorators import tool
from core.tool_types import Display, GameRef, ScoreRef, ToolContext, ToolResult
from services.game.profile import GameProfile
from services.game.rating_format import score_text, song_rating_text
from services.game.registry import profile_for

# 곡 DB에 있는 상수의 범위(츄니즘 ULT까지, maimai 15.0까지)를 넉넉히 덮는다.
_MIN_CONST = 1.0
_MAX_CONST = 17.0
_MAX_SCORE = 1_010_000  # 두 게임 모두 만점(츄니즘 1,010,000점, maimai 달성률 101.0000%)
_MAX_LISTED = 10  # 맞는 상수가 많을 때(낮은 점수에서 곡 레이팅이 0인 경우) 보여 줄 최대 개수


def _profile(ctx: ToolContext) -> GameProfile:
    return profile_for(ctx.game)


def _const_error(const: float) -> ToolResult | None:
    if not _MIN_CONST <= const <= _MAX_CONST:
        return ToolResult(error=f"상수는 {_MIN_CONST}~{_MAX_CONST} 사이여야 해")
    return None


def _score(ctx: ToolContext, score: int) -> str:
    return score_text(score, percent=_profile(ctx).score_is_percent)


def _rank_at(ctx: ToolContext, score: int) -> str | None:
    """그 점수가 이미 넘은 가장 높은 랭크 이름. 어느 랭크에도 못 닿으면 None."""
    reached = [(value, name) for name, value in ctx.terms.rank_scores().items() if value <= score]
    return max(reached)[1] if reached else None


def _rating(profile: GameProfile, score: int, const: float) -> Decimal:
    rating = profile.chart_rating(score, const)
    assert rating is not None  # 상수를 줬으므로 계산 불가가 아니다
    return rating


def _rating_text(profile: GameProfile, rating: Decimal) -> str:
    return song_rating_text(rating, places=profile.song_rating_places)


@tool(slash="레이팅 계산")
async def song_rating(ctx: ToolContext, const: float, score: ScoreRef, game: GameRef | None = None) -> ToolResult:
    """상수와 점수(또는 랭크 이름 — 그 랭크의 기준 점수)로 곡 레이팅을 계산한다. 곡 이름은 모른다."""
    assert isinstance(score, int)
    if (error := _const_error(const)) is not None:
        return error
    profile = _profile(ctx)
    rating = _rating(profile, score, const)
    text = _rating_text(profile, rating)
    score_label = _score(ctx, score)
    return ToolResult(
        data={"game": profile.game, "const": f"{const:.1f}", "score": score, "score_text": score_label,
              "rank": _rank_at(ctx, score), "song_rating": text},
        display=Display(content=f"상수 {const:.1f} · {score_label} → 곡 레이팅 {text}"),
        display_mode="optional",
    )


@tool(slash="레이팅 상수역산")
async def find_const(ctx: ToolContext, rating: float, score: ScoreRef, game: GameRef | None = None) -> ToolResult:
    """곡 레이팅과 점수(또는 랭크 이름)로 그 곡 레이팅이 나오는 상수를 거꾸로 찾는다. 정확히 맞는 상수가 없으면
    바로 아래·위 상수와 그 곡 레이팅을 돌려준다."""
    assert isinstance(score, int)
    profile = _profile(ctx)
    target = Decimal(str(rating))
    score_label = _score(ctx, score)
    matches: list[str] = []
    below: tuple[str, Decimal] | None = None  # 목표보다 낮은 쪽에서 가장 가까운 상수
    above: tuple[str, Decimal] | None = None  # 목표보다 높은 쪽에서 가장 가까운 상수
    for tenths in range(round(_MIN_CONST * 10), round(_MAX_CONST * 10) + 1):
        const = tenths / 10
        value = _rating(profile, score, const)
        if value == target:
            matches.append(f"{const:.1f}")
        elif value < target:
            below = (f"{const:.1f}", value)
        elif above is None:
            above = (f"{const:.1f}", value)
    head = f"{score_label}에서 곡 레이팅 {_rating_text(profile, target)}"
    data: dict[str, object] = {"game": profile.game, "rating": str(rating), "score": score, "score_text": score_label,
                               "consts": matches[:_MAX_LISTED], "match_count": len(matches)}
    if matches:
        shown = ", ".join(matches[:_MAX_LISTED]) + (f" 외 {len(matches) - _MAX_LISTED}개" if len(matches) > _MAX_LISTED else "")
        return ToolResult(data=data, display=Display(content=f"{head} → 상수 {shown}"), display_mode="optional")

    def _near(entry: tuple[str, Decimal] | None) -> dict[str, str] | None:
        return None if entry is None else {"const": entry[0], "song_rating": _rating_text(profile, entry[1])}

    data["below"], data["above"] = _near(below), _near(above)
    parts: list[str] = []
    for label, entry in (("아래", below), ("위", above)):
        if entry is not None:
            parts.append(f"{label}: 상수 {entry[0]} → {_rating_text(profile, entry[1])}")
    detail = " / ".join(parts) if parts else "범위 안에 가까운 상수가 없어"
    return ToolResult(
        data=data, display=Display(content=f"{head}가 되는 상수는 없어. {detail}"), display_mode="optional"
    )


@tool(slash="레이팅 필요점수")
async def needed_score(ctx: ToolContext, const: float, rating: float, game: GameRef | None = None) -> ToolResult:
    """상수와 목표 곡 레이팅으로, 그 곡 레이팅 이상이 되는 가장 낮은 점수를 계산한다. 만점을 쳐도 못 닿으면 못 닿는다고 알려 준다."""
    if (error := _const_error(const)) is not None:
        return error
    profile = _profile(ctx)
    target = Decimal(str(rating))
    best = _rating(profile, _MAX_SCORE, const)
    if best < target:
        return ToolResult(
            data={"game": profile.game, "const": f"{const:.1f}", "reachable": False, "max_song_rating": _rating_text(profile, best)},
            display=Display(content=f"상수 {const:.1f}는 만점을 쳐도 곡 레이팅 {_rating_text(profile, best)}까지라 "
                                    f"{_rating_text(profile, target)}에는 못 닿아"),
            display_mode="optional",
        )
    low, high = 0, _MAX_SCORE  # 점수가 오르면 곡 레이팅은 줄지 않는다 — 이진 탐색으로 처음 목표에 닿는 점수를 찾는다
    while low < high:
        middle = (low + high) // 2
        if _rating(profile, middle, const) >= target:
            high = middle
        else:
            low = middle + 1
    reached = _rating(profile, low, const)
    score_label = _score(ctx, low)
    rank = _rank_at(ctx, low)
    rank_note = f"({rank} 이상)" if rank is not None else ""
    return ToolResult(
        data={"game": profile.game, "const": f"{const:.1f}", "reachable": True, "needed_score": low, "score_text": score_label,
              "rank": rank, "song_rating_at_score": _rating_text(profile, reached)},
        display=Display(
            content=f"상수 {const:.1f}에서 곡 레이팅 {_rating_text(profile, target)} 이상이 되려면 "
                    f"{score_label}{rank_note}부터야 (그 점수의 곡 레이팅 {_rating_text(profile, reached)})"
        ),
        display_mode="optional",
    )
