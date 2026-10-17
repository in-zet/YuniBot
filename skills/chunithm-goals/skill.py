# skills/chunithm-goals/skill.py
from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.decorators import on, tool
from core.events import Section
from core.tool_types import (
    ChartRef,
    DifficultyRef,
    Display,
    PlayerRef,
    RankRef,
    ScoreRef,
    ToolContext,
    ToolResult,
)
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.records import PbRow, RatingDeltas
from services.chunithm.records_calc import chart_rating
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY
from services.chunithm.terms import ResolvedDifficulty, ResolvedRank
from services.common.players import Player

# skills/chunithm-goals는 폴더 이름에 하이픈이 있어 보통의 패키지 import 경로로 쓸 수 없다
# (skills/account-link/skill.py가 쓰는 것과 같은 이유) — 같은 폴더의 models.py/goals_calc.py를
# 파일 경로로 직접 불러온다.
_SKILL_DIR = Path(__file__).parent


def _load_sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_chunithm_goals_{name}", _SKILL_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass(kw_only=True)가 모듈 전역을 되찾아야 한다
    spec.loader.exec_module(module)
    return module


models = _load_sibling("models")
calc = _load_sibling("goals_calc")

_SYNCED_TAG = "chunithm-synced"
_SYNC_SECTION_ORDER = 30
_SONG_VIEW_ORDER = 10
_PLAN_ORDER = 10
_PLAN_LIMIT = 5


class GoalKind(StrEnum):
    RATING = "rating"
    OVERPOWER = "overpower"
    FOLDER = "folder"
    SONG = "song"
    FREEFORM = "freeform"


class GoalStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    ACHIEVED = "achieved"
    EXPIRED = "expired"
    STOPPED = "stopped"


class RatingMetric(StrEnum):
    COMPUTED = "computed_rating"
    BEST30 = "best30_average"
    NEW20 = "new20_average"


class OverpowerMode(StrEnum):
    VALUE = "value"
    PERCENTAGE = "percentage"


class OverpowerScope(StrEnum):
    OVERALL = "overall"
    FOLDER = "folder"


class FolderCondition(StrEnum):
    PLAYED = "played"
    CLEARED = "cleared"
    RANK = "rank"
    FULL_COMBO = "full_combo"
    ALL_JUSTICE = "all_justice"


class DurationTag(StrEnum):
    SHORT = "short"
    MID = "mid"
    LONG = "long"


# ---------------------------------------------------------------- 공통 도우미


async def _target_player(ctx: ToolContext, player: PlayerRef | None) -> Player | ToolResult:
    if player is None:
        me = await ctx.players.get_player(ctx.player_id)
        if me is None:
            return ToolResult(error="플레이어 정보를 찾지 못했어.")
        return me
    assert isinstance(player, Player)
    return player


def _difficulty_code(difficulty: DifficultyRef) -> str:
    """해석된 난이도(정식 이름, 예: MASTER)를 records/songdb가 쓰는 짧은 코드(MAS)로 바꾼다."""
    assert isinstance(difficulty, ResolvedDifficulty)
    return LONG_TO_SHORT_DIFFICULTY.get(difficulty.canonical, difficulty.canonical)


def _rank(rank: RankRef | None) -> ResolvedRank | None:
    assert rank is None or isinstance(rank, ResolvedRank)
    return rank


def _score(score: ScoreRef | None) -> int | None:
    """ScoreRef 해석기(terms.resolve_score)는 숫자·랭크 이름·점수가 고정된 램프 이름을 0~1,010,000
    정수 점수로 바꿔 준다."""
    if score is None:
        return None
    assert isinstance(score, int)
    return int(score)


def _metric_value(deltas: RatingDeltas, metric: RatingMetric) -> Decimal | None:
    return {
        RatingMetric.COMPUTED: deltas.computed_rating,
        RatingMetric.BEST30: deltas.best30_average,
        RatingMetric.NEW20: deltas.new20_average,
    }[metric]


def _default_notify_offsets(duration_tag: DurationTag | None) -> list[int]:
    offsets = [7, 1, 0]
    if duration_tag == DurationTag.LONG:
        offsets = [30, *offsets]
    return offsets


def _goal_label(goal: Any) -> str:
    ref = goal.target_ref
    if goal.kind == "song":
        target = int(goal.target_value) if goal.target_value is not None else 0
        return f"{ref['song_title']} {ref['difficulty']}  {target}점"
    if goal.kind == "rating":
        return f"레이팅 {goal.target_value}"
    if goal.kind == "overpower":
        return f"오버파워 {goal.target_value}"
    if goal.kind == "folder":
        selector = ref.get("selector", "const")
        if selector == "level":
            scope = f"레벨 {ref['level']}"
        elif selector == "version":
            scope = f"버전 {ref['version']}"
        else:
            scope = f"{ref['min_const']}~{ref['max_const']}"
        return f"폴더({scope} {ref['difficulty']})  {ref['condition']}"
    return ref.get("description", "자유 목표")


# records_pb.combo_lamp 값(vendored ComboLamp의 str): NONE / FULL COMBO / ALL JUSTICE / AJC.
# 풀콤보 조건은 올 저스티스 이상도 채운 것으로 본다. chain_lamp(FULL CHAIN 등)는 다른 램프다.
_ALL_JUSTICE_LAMPS = frozenset({"ALL JUSTICE", "AJC"})


def _row_matches_condition(row: PbRow, condition: FolderCondition, rank_min_score: int | None) -> bool:
    """rank_min_score는 RankRef 해석기가 준 그 랭크의 기준 점수다(랭크 이름을 여기서 비교하지 않는다)."""
    if condition == FolderCondition.PLAYED:
        return True
    if condition == FolderCondition.CLEARED:
        return row.clear_lamp not in ("", "NONE", "FAILED")
    if condition == FolderCondition.FULL_COMBO:
        return row.combo_lamp not in ("", "NONE")
    if condition == FolderCondition.ALL_JUSTICE:
        return row.combo_lamp in _ALL_JUSTICE_LAMPS
    if condition == FolderCondition.RANK:
        return rank_min_score is not None and row.score >= rank_min_score
    return False


def _folder_hits(ctx: ToolContext, target_ref: dict[str, Any]) -> list[Any]:
    """폴더 정의(상수 범위/표기 레벨/수록 버전 중 하나 + 난이도)에 맞는 채보를 songdb에서
    찾는다. 설계서("폴더 정의"는 이 셋 중 하나에 난이도를 조합한다, 장르는 이번 범위 밖)에
    맞춰 difficulty는 항상 요구한다 — songdb.find_by_version 자체는 difficulty를 비워
    "그 버전 전체 난이도"도 찾을 수 있지만, 폴더 목표에서는 쓰지 않는다."""
    selector = target_ref["selector"]
    difficulty = target_ref["difficulty"]
    if selector == "const":
        return ctx.songdb.find_by_const(
            min_const=target_ref["min_const"], max_const=target_ref["max_const"], difficulty=difficulty
        )
    if selector == "level":
        return ctx.songdb.find_by_level(level=target_ref["level"], difficulty=difficulty)
    return ctx.songdb.find_by_version(version=target_ref["version"], difficulty=difficulty)


async def _folder_counts(ctx: ToolContext, target_ref: dict[str, Any], *, player_id: str) -> tuple[int, int]:
    hits = _folder_hits(ctx, target_ref)
    total = len(hits)
    if total == 0:
        return 0, 0
    rows = await ctx.records.all_pb_rows(player_id)
    rows_by_key = {(r.song_title, r.difficulty): r for r in rows}
    condition = FolderCondition(target_ref["condition"])
    rank_min_score: int | None = target_ref.get("rank_min_score")
    matched = sum(
        1
        for hit in hits
        if (row := rows_by_key.get((hit.song.title, target_ref["difficulty"]))) is not None
        and _row_matches_condition(row, condition, rank_min_score)
    )
    return total, matched


async def _record_progress_and_maybe_complete(
    ctx: ToolContext, goal: Any, sync_id: str | None, current: float
) -> str | None:
    """진행 기록을 한 행 남기고, 이번에 처음 달성했으면 완료 처리 + 알림을 낸다. 라벨을 돌려주면
    이번에 달성했다는 뜻이다(완료 목록에 쓴다)."""
    baseline = Decimal(str(goal.baseline_value if goal.baseline_value is not None else 0))
    target = Decimal(str(goal.target_value if goal.target_value is not None else current))
    ratio = calc.achievement_ratio(Decimal(str(current)), baseline, target)
    await models.insert_progress(
        ctx.db, id=str(uuid.uuid4()), goal_id=goal.id, sync_id=sync_id,
        recorded_at=datetime.now(UTC).isoformat(), value=float(current), achievement_ratio=float(ratio),
    )
    if ratio >= 1:
        await models.update_goal(ctx.db, goal.id, status=GoalStatus.ACHIEVED.value)
        label = _goal_label(goal)
        await ctx.emit(
            "chunithm.goal.completed",
            {
                "player_id": goal.player_id, "goal_id": goal.id, "kind": goal.kind, "label": label,
                "created_at": goal.created_at, "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        return label
    return None


async def _project_rating_for(ctx: ToolContext, player_id: str) -> Decimal | None:
    """곡별 목표(To-Do)를 모두 달성했을 때의 예상 레이팅(goals_calc.project_rating_with_todos).
    달성 목표가 하나도 없으면 None이다."""
    best_rows, new_rows = await ctx.records.current_frames(player_id)
    best_ratings = [r.rating for r in best_rows if r.rating is not None]
    new_ratings = [r.rating for r in new_rows if r.rating is not None]
    song_goals = await models.list_goals_for_player(
        ctx.db, player_id, kind=GoalKind.SONG.value, statuses=[GoalStatus.IN_PROGRESS.value]
    )
    todo_targets: list[tuple[bool, Decimal]] = []
    for g in song_goals:
        title, dif = g.target_ref["song_title"], g.target_ref["difficulty"]
        const = ctx.songdb.const_for(title, dif)
        if const is None or g.target_value is None:
            continue
        rating = chart_rating(int(g.target_value), const)
        if rating is None:
            continue
        todo_targets.append((ctx.songdb.is_new_for(title), rating))
    if not todo_targets:
        return None
    return calc.project_rating_with_todos(best_ratings, new_ratings, todo_targets)


# ---------------------------------------------------------------- 도구


@tool(slash="목표 추가", mutates=True)
async def set_goal(
    ctx: ToolContext,
    kind: GoalKind,
    *,
    metric: RatingMetric = RatingMetric.COMPUTED,
    target_value: float | None = None,
    chart: ChartRef | None = None,
    target_score: ScoreRef | None = None,
    folder_min_const: float | None = None,
    folder_max_const: float | None = None,
    folder_level: str | None = None,
    folder_version: str | None = None,
    folder_difficulty: DifficultyRef | None = None,
    folder_condition: FolderCondition = FolderCondition.CLEARED,
    folder_rank: RankRef | None = None,
    overpower_mode: OverpowerMode = OverpowerMode.PERCENTAGE,
    overpower_scope: OverpowerScope = OverpowerScope.OVERALL,
    description: str | None = None,
    due_at: str | None = None,
    duration_tag: DurationTag | None = None,
    parent_goal_id: str | None = None,
    public: bool = False,
) -> ToolResult:
    """목표를 만든다. kind별로 필요한 인자: rating은 metric+target_value(현재값보다 커야 한다),
    overpower는 overpower_mode+target_value(전체만 지원, overpower_scope=folder는 아직 안 됨,
    현재값보다 커야 한다), folder는 folder_difficulty + (folder_min_const와 folder_max_const) 또는
    folder_level 또는 folder_version 중 하나(+folder_condition, rank 조건이면 folder_rank), song은
    chart(곡+난이도 원문 그대로, 예: "크로노 마스")+target_score(점수 숫자나 랭크 이름을 원문
    그대로 — 점수 환산은 해석기가 한다, 이미 그 점수를 넘겼으면 에러), freeform은 description.
    due_at은 ISO 날짜(YYYY-MM-DD)다. 레이팅·오버파워·폴더 목표는 CHUNITHM-NET 동기화(chunithm-synced)가 있어야
    만들 수 있다. 레이팅 최종 목표는 플레이어당 하나뿐이다 — 이미 있으면 새로 만들지 않고
    에러를 돌려주고 `update`를 쓰라고 안내한다(설계서에 없던 세부, SKILL.md 본문 참고)."""
    if kind in (GoalKind.RATING, GoalKind.OVERPOWER, GoalKind.FOLDER) and _SYNCED_TAG not in ctx.tags:
        return ToolResult(error="그 목표는 CHUNITHM-NET 동기화가 돼 있어야 만들 수 있어. /동기화를 먼저 해 줘.")

    now = datetime.now(UTC).isoformat()
    goal_id = str(uuid.uuid4())
    target_ref: dict[str, Any]
    baseline_value: float | None

    if kind == GoalKind.RATING:
        if target_value is None:
            return ToolResult(error="목표 레이팅 값을 알려 줘.")
        if await models.find_rating_goal(ctx.db, ctx.player_id) is not None:
            return ToolResult(error="이미 레이팅 목표가 있어. `/목표 수정`을 써 줘.")
        deltas = await ctx.records.rating_deltas(ctx.player_id)
        baseline = _metric_value(deltas, metric) if deltas is not None else None
        if baseline is None:
            return ToolResult(error="아직 동기화한 기록이 없어서 기준값을 잡을 수 없어.")
        target_ref = {"metric": metric.value}
        baseline_value = float(baseline)
    elif kind == GoalKind.OVERPOWER:
        if overpower_scope != OverpowerScope.OVERALL:
            return ToolResult(error="폴더별 오버파워 목표는 아직 지원하지 않아. 전체 오버파워만 가능해.")
        if target_value is None:
            return ToolResult(error="목표 오버파워 값(또는 퍼센트)을 알려 줘.")
        overpower = await ctx.records.latest_overpower(ctx.player_id)
        if overpower is None:
            return ToolResult(error="아직 동기화한 기록이 없어서 기준값을 잡을 수 없어.")
        value, percentage = overpower
        current = percentage if overpower_mode == OverpowerMode.PERCENTAGE else value
        if current is None:
            return ToolResult(error="오버파워 기준값을 못 찾았어.")
        target_ref = {"mode": overpower_mode.value}
        baseline_value = float(current)
    elif kind == GoalKind.FOLDER:
        if folder_difficulty is None:
            return ToolResult(error="폴더는 난이도(folder_difficulty)가 필요해.")
        difficulty_code = _difficulty_code(folder_difficulty)
        rank = _rank(folder_rank)
        rank_fields: dict[str, Any] = {
            "condition": folder_condition.value,
            "rank": rank.canonical if rank is not None else None,
            "rank_min_score": rank.min_score if rank is not None else None,
        }
        selectors_given = [
            s for s in (folder_min_const is not None or folder_max_const is not None, folder_level is not None,
                        folder_version is not None)
            if s
        ]
        if len(selectors_given) != 1:
            return ToolResult(
                error="폴더는 상수 범위(folder_min_const/folder_max_const), 표기 레벨(folder_level), "
                "수록 버전(folder_version) 중 하나만 줘야 해."
            )
        if folder_min_const is not None or folder_max_const is not None:
            if folder_min_const is None or folder_max_const is None:
                return ToolResult(error="상수 범위는 folder_min_const와 folder_max_const를 같이 줘야 해.")
            target_ref = {
                "selector": "const", "min_const": folder_min_const, "max_const": folder_max_const,
                "difficulty": difficulty_code, **rank_fields,
            }
        elif folder_level is not None:
            target_ref = {"selector": "level", "level": folder_level, "difficulty": difficulty_code, **rank_fields}
        else:
            assert folder_version is not None
            target_ref = {
                "selector": "version", "version": folder_version, "difficulty": difficulty_code, **rank_fields,
            }
        if folder_condition == FolderCondition.RANK and rank is None:
            return ToolResult(error="랭크 조건이면 목표 랭크(folder_rank)를 알려 줘.")
        total, matched = await _folder_counts(ctx, target_ref, player_id=ctx.player_id)
        if total == 0:
            return ToolResult(error="그 조건에 맞는 채보가 없어.")
        baseline_value = float(matched)
        target_value = float(total)
    elif kind == GoalKind.SONG:
        score = _score(target_score)
        if chart is None or score is None:
            return ToolResult(error="곡과 난이도(chart), 목표 점수(target_score)가 필요해.")
        assert isinstance(chart, ResolvedChart)
        song_title, difficulty = chart.song.title, chart.chart.difficulty
        if await models.find_song_goal(ctx.db, ctx.player_id, song_title=song_title, difficulty=difficulty):
            return ToolResult(error="그 채보에는 이미 진행 중인 투두가 있어.")
        row = await ctx.records.pb_row(ctx.player_id, song_title=song_title, difficulty=difficulty)
        if row is not None and row.score >= score:
            return ToolResult(error="이미 그 목표를 달성했어.")
        target_ref = {"song_title": song_title, "difficulty": difficulty}
        target_value = float(score)
        baseline_value = float(row.score) if row is not None else 0.0
    else:
        if description is None:
            return ToolResult(error="자유 목표는 설명이 필요해.")
        target_ref = {"description": description}
        baseline_value = None

    await models.insert_goal(
        ctx.db, id=goal_id, player_id=ctx.player_id, kind=kind.value, target_ref=target_ref,
        target_value=target_value, baseline_value=baseline_value, created_at=now, due_at=due_at,
        duration_tag=duration_tag.value if duration_tag is not None else None, parent_goal_id=parent_goal_id,
        public=public, notify_offsets=_default_notify_offsets(duration_tag),
    )
    return ToolResult(
        data={"goal_id": goal_id, "kind": kind.value, "target_ref": target_ref, "target_value": target_value},
        display=Display(content=f"목표를 만들었어. ({_goal_label(await models.fetch_goal(ctx.db, goal_id))})"),
        display_mode="optional",
    )


@tool(slash="목표 목록")
async def list_goals(ctx: ToolContext, player: PlayerRef | None = None, kind: GoalKind | None = None) -> ToolResult:
    """목표 목록과 달성률을 보여준다. player를 주면 그 사람의 공개 목표만(본인이면 전체)."""
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    rows = await models.list_goals_for_player(ctx.db, target.id, kind=kind.value if kind is not None else None)
    if target.id != ctx.player_id:
        if not target.goals_public:
            return ToolResult(error="그 사람은 목표를 비공개로 해 놨어.")
        rows = [r for r in rows if r.public]

    items = [
        {
            "goal_id": r.id, "kind": r.kind, "target_ref": r.target_ref, "target_value": r.target_value,
            "baseline_value": r.baseline_value, "status": r.status, "due_at": r.due_at,
            "duration_tag": r.duration_tag, "public": r.public,
        }
        for r in rows
    ]
    lines = [f"[{r.status}] {_goal_label(r)}" for r in rows]
    return ToolResult(
        data={"player_id": target.id, "goals": items},
        display=Display(content="\n".join(lines) if lines else "목표가 없어."),
    )


@tool(slash="목표 수정", mutates=True)
async def update(
    ctx: ToolContext,
    goal_id: str,
    *,
    target_value: float | None = None,
    due_at: str | None = None,
    duration_tag: DurationTag | None = None,
    parent_goal_id: str | None = None,
    status: GoalStatus | None = None,
    public: bool | None = None,
) -> ToolResult:
    """목표값·기한·기간 꼬리표·상위 목표·상태·공개 여부를 바꾼다(자유 목표의 진행은 상태로 기록한다).
    본인 목표만 바꿀 수 있다."""
    goal = await models.fetch_goal(ctx.db, goal_id)
    if goal is None or goal.player_id != ctx.player_id:
        return ToolResult(error="그 목표를 찾지 못했어.")

    fields: dict[str, Any] = {}
    if target_value is not None:
        fields["target_value"] = target_value
    if due_at is not None:
        fields["due_at"] = due_at
    if duration_tag is not None:
        fields["duration_tag"] = duration_tag.value
    if parent_goal_id is not None:
        fields["parent_goal_id"] = parent_goal_id
    if status is not None:
        fields["status"] = status.value
    if public is not None:
        fields["public"] = public
    if not fields:
        return ToolResult(error="바꿀 내용이 없어.")

    await models.update_goal(ctx.db, goal_id, **fields)
    return ToolResult(
        data={"goal_id": goal_id, "updated": sorted(fields)},
        display=Display(content="목표를 수정했어."),
        display_mode="optional",
    )


@tool(slash="목표 진행")
async def progress(ctx: ToolContext, goal_id: str) -> ToolResult:
    """목표 하나의 진행 기록과 페이스를 보여준다. 레이팅 목표면 곡별 목표(To-Do)를 모두
    달성했을 때의 예상 레이팅도 함께 보여준다."""
    goal = await models.fetch_goal(ctx.db, goal_id)
    if goal is None:
        return ToolResult(error="그 목표를 찾지 못했어.")
    if goal.player_id != ctx.player_id:
        owner = await ctx.players.get_player(goal.player_id)
        if owner is None or not owner.goals_public or not goal.public:
            return ToolResult(error="그 목표는 비공개야.")

    history = await models.list_progress(ctx.db, goal_id)
    data: dict[str, Any] = {
        "goal_id": goal.id, "kind": goal.kind, "status": goal.status,
        "history": [
            {"sync_id": p.sync_id, "recorded_at": p.recorded_at, "value": p.value, "achievement_ratio": p.achievement_ratio}
            for p in history
        ],
    }
    lines = [f"[{goal.status}] {_goal_label(goal)}"]
    if history:
        latest = history[-1]
        lines.append(f"최근 진행  {latest.value}  (달성률 {latest.achievement_ratio})")

    if goal.kind == GoalKind.RATING.value:
        projected = await _project_rating_for(ctx, goal.player_id)
        data["projected_rating"] = str(projected) if projected is not None else None
        if projected is not None:
            lines.append(f"곡별 목표를 모두 달성하면 예상 레이팅 {projected}")

    return ToolResult(data=data, display=Display(content="\n".join(lines)), display_mode="optional")


@tool(slash="목표 삭제", mutates=True, confirm="bulk")
async def delete(ctx: ToolContext, goal_ids: str, *, dry_run: bool = False) -> ToolResult:
    """목표를 지운다(쉼표로 여러 개 지정할 수 있다). 본인 목표만 지울 수 있다. 2개 이상을
    지울 때는 실행기가 지울 개수를 보여 주고 확인 버튼을 받은 뒤에 지운다."""
    ids = [g.strip() for g in goal_ids.split(",") if g.strip()]
    owned: list[str] = []
    for gid in ids:
        goal = await models.fetch_goal(ctx.db, gid)
        if goal is not None and goal.player_id == ctx.player_id:
            owned.append(gid)
    if not owned:
        return ToolResult(error="지울 수 있는 목표가 없어.")
    if dry_run:
        return ToolResult(
            data={"count": len(owned), "goal_ids": owned},
            display=Display(content=f"목표 {len(owned)}개를 지울게."),
            display_mode="optional",
        )
    count = await models.delete_goals(ctx.db, owned)
    return ToolResult(
        data={"deleted_count": count, "goal_ids": owned},
        display=Display(content=f"{count}개 지웠어."),
        display_mode="optional",
    )


@tool(slash="투두 추가", mutates=True)
async def todo_add(
    ctx: ToolContext, chart: ChartRef, target_score: ScoreRef, *, public: bool = False,
    due_at: str | None = None,
) -> ToolResult:
    """곡별 목표(To-Do) 한 건을 추가한다. chart는 곡+난이도 원문(예: "크로노 마스"), target_score는
    점수 숫자나 랭크 이름(SSS, SSS+ 등)을 플레이어가 말한 그대로 넘긴다 — 점수 환산은 해석기가
    하므로 직접 계산하지 않는다. 이미 그 점수를 넘긴 채보면 에러다."""
    return await set_goal(
        ctx, GoalKind.SONG, chart=chart, target_score=target_score, public=public, due_at=due_at,
    )


@tool(slash="투두 일괄", mutates=True, confirm="bulk")
async def todo_bulk(
    ctx: ToolContext,
    min_const: float,
    max_const: float,
    difficulty: DifficultyRef,
    target_score: ScoreRef,
    *,
    condition: FolderCondition = FolderCondition.RANK,
    rank: RankRef | None = None,
    public: bool = False,
    dry_run: bool = False,
) -> ToolResult:
    """상수 범위·난이도·현재 랭크(또는 클리어/풀콤보/올저스티스) 조건을 아직 못 채운 채보를 모아
    To-Do로 일괄 추가한다. difficulty·rank·target_score는 플레이어가 말한 그대로(예: "마스",
    "SSS") 넘긴다 — 해석기가 정식 값과 점수로 바꾼다. 이미 target_score를 넘긴 채보도 뺀다.
    조건이 played가 아니면 CHUNITHM-NET 동기화(chunithm-synced)가 있어야 한다. 대상이 2개
    이상이면 실행기가 먼저 대상 개수를 보여 주고 확인 버튼을 받은 뒤에 추가한다(0~1개면 바로
    추가한다)."""
    if condition != FolderCondition.PLAYED and _SYNCED_TAG not in ctx.tags:
        return ToolResult(error="그 조건을 쓰려면 CHUNITHM-NET 동기화가 돼 있어야 해.")
    resolved_rank = _rank(rank)
    if condition == FolderCondition.RANK and resolved_rank is None:
        return ToolResult(error="랭크 조건이면 목표 랭크(rank)를 알려 줘.")
    score = _score(target_score)
    assert score is not None
    rank_min_score = resolved_rank.min_score if resolved_rank is not None else None
    difficulty_code = _difficulty_code(difficulty)

    hits = ctx.songdb.find_by_const(min_const=min_const, max_const=max_const, difficulty=difficulty_code)
    rows = await ctx.records.all_pb_rows(ctx.player_id)
    rows_by_key = {(r.song_title, r.difficulty): r for r in rows}

    targets: list[str] = []
    for hit in hits:
        row = rows_by_key.get((hit.song.title, difficulty_code))
        if row is not None and _row_matches_condition(row, condition, rank_min_score):
            continue  # 이미 조건을 채웠다
        if row is not None and row.score >= score:
            continue  # 조건은 못 채웠어도 목표 점수는 이미 넘겼다
        if await models.find_song_goal(ctx.db, ctx.player_id, song_title=hit.song.title, difficulty=difficulty_code):
            continue  # 이미 투두가 있다
        targets.append(hit.song.title)

    if not targets:
        return ToolResult(
            data={"count": 0, "song_titles": []},
            display=Display(content="그 조건에 맞는 곡이 없어."),
            display_mode="required",
        )
    if dry_run:
        return ToolResult(
            data={"count": len(targets), "song_titles": targets},
            display=Display(content=f"투두 {len(targets)}개를 추가할게: {', '.join(targets)}"),
            display_mode="required",
        )

    now = datetime.now(UTC).isoformat()
    created: list[str] = []
    for title in targets:
        goal_id = str(uuid.uuid4())
        row = rows_by_key.get((title, difficulty_code))
        await models.insert_goal(
            ctx.db, id=goal_id, player_id=ctx.player_id, kind=GoalKind.SONG.value,
            target_ref={"song_title": title, "difficulty": difficulty_code}, target_value=float(score),
            baseline_value=float(row.score) if row is not None else 0.0, created_at=now, due_at=None,
            duration_tag=None, parent_goal_id=None, public=public, notify_offsets=_default_notify_offsets(None),
        )
        created.append(goal_id)

    return ToolResult(
        data={"count": len(created), "song_titles": targets, "goal_ids": created},
        display=Display(content=f"{len(created)}개 투두를 추가했어."),
        display_mode="required",
    )


@tool(slash="투두 목록")
async def todo_list(ctx: ToolContext, sort: str = "const") -> ToolResult:
    """미완료 To-Do 목록을 상수순(const, 기본) 또는 남은 점수순(remaining)으로 보여준다."""
    rows = await models.list_goals_for_player(
        ctx.db, ctx.player_id, kind=GoalKind.SONG.value, statuses=[GoalStatus.IN_PROGRESS.value]
    )
    items: list[dict[str, Any]] = []
    for g in rows:
        title, dif = g.target_ref["song_title"], g.target_ref["difficulty"]
        pb = await ctx.records.pb_row(ctx.player_id, song_title=title, difficulty=dif)
        current = pb.score if pb is not None else 0
        const = ctx.songdb.const_for(title, dif)
        note_count = ctx.songdb.note_count_for(title, dif)
        target = int(g.target_value) if g.target_value is not None else 0
        remaining = ctx.records.remaining_to_target(note_count, current, target)
        items.append(
            {
                "goal_id": g.id, "song_title": title, "difficulty": dif, "const": const,
                "current_score": current, "target_score": target,
                "remaining_score": remaining[0] if remaining is not None else None,
                "remaining_attacks": str(remaining[1]) if remaining is not None else None,
            }
        )

    if sort == "remaining":
        items.sort(key=lambda i: (i["remaining_attacks"] is None, Decimal(i["remaining_attacks"] or "0")))
    else:
        items.sort(key=lambda i: (i["const"] is None, -(i["const"] or 0.0)))

    def _todo_line(i: dict[str, Any]) -> str:
        const_text = "" if i["const"] is None else f" {i['const']}"
        attacks_text = "계산 불가" if i["remaining_attacks"] is None else f"{i['remaining_attacks']}어택"
        return (
            f"{i['song_title']} {i['difficulty']}{const_text}  "
            f"{i['current_score']:,} → {i['target_score']:,}  ({attacks_text})"
        )

    lines = [_todo_line(i) for i in items]
    return ToolResult(
        data={"todos": items}, display=Display(content="\n".join(lines) if lines else "투두가 없어."),
        display_mode="required",
    )


@tool(slash="투두 완료", mutates=True)
async def todo_done(ctx: ToolContext, goal_id: str) -> ToolResult:
    """To-Do를 수동으로 완료 처리한다."""
    goal = await models.fetch_goal(ctx.db, goal_id)
    if goal is None or goal.player_id != ctx.player_id or goal.kind != GoalKind.SONG.value:
        return ToolResult(error="그 투두를 찾지 못했어.")
    await models.update_goal(ctx.db, goal_id, status=GoalStatus.ACHIEVED.value)
    await ctx.emit(
        "chunithm.goal.completed",
        {
            "player_id": ctx.player_id, "goal_id": goal_id, "kind": goal.kind, "label": _goal_label(goal),
            "created_at": goal.created_at, "completed_at": datetime.now(UTC).isoformat(),
        },
    )
    return ToolResult(
        data={"goal_id": goal_id, "status": GoalStatus.ACHIEVED.value},
        display=Display(content="완료 처리했어."),
        display_mode="optional",
    )


# ---------------------------------------------------------------- 이벤트 핸들러


@on("chunithm.sync.completed")
async def on_sync_completed(ctx: ToolContext, payload: dict[str, Any]) -> Section:
    """동기화 리포트의 목표 점검 조각(order 30): 곡별 목표 자동 완료, 레이팅·오버파워·폴더
    목표의 진행 기록(과 달성 시 자동 완료)."""
    player_id: str = payload["player_id"]
    sync_id: str | None = payload.get("sync_id")
    diff = await ctx.records.sync_diff(sync_id) if sync_id else None
    update_by_key = {(u.song_title, u.difficulty): u for u in (diff.chart_updates if diff is not None else [])}

    completed: list[str] = []
    lines: list[str] = []

    song_goals = await models.list_goals_for_player(
        ctx.db, player_id, kind=GoalKind.SONG.value, statuses=[GoalStatus.IN_PROGRESS.value]
    )
    for g in song_goals:
        title, dif = g.target_ref["song_title"], g.target_ref["difficulty"]
        chart_update = update_by_key.get((title, dif))
        if chart_update is None or g.target_value is None or chart_update.new_score < g.target_value:
            continue
        await models.update_goal(ctx.db, g.id, status=GoalStatus.ACHIEVED.value)
        label = _goal_label(g)
        await ctx.emit(
            "chunithm.goal.completed",
            {
                "player_id": player_id, "goal_id": g.id, "kind": "song", "label": label,
                "created_at": g.created_at, "completed_at": datetime.now(UTC).isoformat(),
            },
        )
        completed.append(label)
        lines.append(f"달성  {label}")

    rating_goal = await models.find_rating_goal(ctx.db, player_id)
    if rating_goal is not None:
        deltas = await ctx.records.rating_deltas(player_id)
        current = _metric_value(deltas, RatingMetric(rating_goal.target_ref["metric"])) if deltas is not None else None
        if current is not None:
            label = await _record_progress_and_maybe_complete(ctx, rating_goal, sync_id, float(current))
            if label is not None:
                completed.append(label)
                lines.append(f"달성  {label}")

    for g in await models.list_goals_for_player(
        ctx.db, player_id, kind=GoalKind.OVERPOWER.value, statuses=[GoalStatus.IN_PROGRESS.value]
    ):
        overpower = await ctx.records.latest_overpower(player_id)
        if overpower is None:
            continue
        value, percentage = overpower
        current = percentage if g.target_ref.get("mode") == "percentage" else value
        if current is None:
            continue
        label = await _record_progress_and_maybe_complete(ctx, g, sync_id, float(current))
        if label is not None:
            completed.append(label)
            lines.append(f"달성  {label}")

    for g in await models.list_goals_for_player(
        ctx.db, player_id, kind=GoalKind.FOLDER.value, statuses=[GoalStatus.IN_PROGRESS.value]
    ):
        total, matched = await _folder_counts(ctx, g.target_ref, player_id=player_id)
        ratio = calc.folder_progress(total, matched)
        await models.insert_progress(
            ctx.db, id=str(uuid.uuid4()), goal_id=g.id, sync_id=sync_id,
            recorded_at=datetime.now(UTC).isoformat(), value=float(matched),
            achievement_ratio=float(ratio) if ratio is not None else None,
        )
        if ratio is not None and ratio >= 1:
            await models.update_goal(ctx.db, g.id, status=GoalStatus.ACHIEVED.value)
            label = _goal_label(g)
            await ctx.emit(
                "chunithm.goal.completed",
                {
                    "player_id": player_id, "goal_id": g.id, "kind": "folder", "label": label,
                    "created_at": g.created_at, "completed_at": datetime.now(UTC).isoformat(),
                },
            )
            completed.append(label)
            lines.append(f"달성  {label}")

    return Section(
        order=_SYNC_SECTION_ORDER,
        display=Display(content="\n".join(lines) if lines else "목표 변화 없음"),
        data={"kind": "goal_check", "completed": completed},
        stage="great" if completed else None,
    )


@on("chunithm.song.viewed")
async def on_song_viewed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """그 곡에 내 To-Do가 있으면 상태와 남은 어택을 붙인다."""
    player_id: str = payload["player_id"]
    title: str = payload["song_title"]
    dif: str = payload["difficulty"]
    goal = await models.find_song_goal(ctx.db, player_id, song_title=title, difficulty=dif)
    if goal is None:
        return None

    pb = await ctx.records.pb_row(player_id, song_title=title, difficulty=dif)
    current = pb.score if pb is not None else 0
    note_count = ctx.songdb.note_count_for(title, dif)
    target = int(goal.target_value) if goal.target_value is not None else 0
    remaining = ctx.records.remaining_to_target(note_count, current, target)
    if remaining is None:
        content = f"투두 목표 {target:,}점  (남은 양 계산 불가)"
        data: dict[str, Any] = {"goal_id": goal.id, "target_score": target, "remaining_score": None, "remaining_attacks": None}
    else:
        remaining_score, remaining_attacks = remaining
        content = f"투두 목표 {target:,}점  남은 {remaining_score:,}점 ({remaining_attacks}어택)"
        data = {
            "goal_id": goal.id, "target_score": target, "remaining_score": remaining_score,
            "remaining_attacks": str(remaining_attacks),
        }
    return Section(order=_SONG_VIEW_ORDER, display=Display(content=content), data={"kind": "goal_todo", **data})


@on("chunithm.plan.requested")
async def on_plan_requested(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """가까운(남은 어택이 작은 순) To-Do 몇 개를 보여준다."""
    player_id: str = payload["player_id"]
    rows = await models.list_goals_for_player(
        ctx.db, player_id, kind=GoalKind.SONG.value, statuses=[GoalStatus.IN_PROGRESS.value]
    )
    if not rows:
        return None

    items: list[dict[str, Any]] = []
    for g in rows:
        title, dif = g.target_ref["song_title"], g.target_ref["difficulty"]
        pb = await ctx.records.pb_row(player_id, song_title=title, difficulty=dif)
        current = pb.score if pb is not None else 0
        note_count = ctx.songdb.note_count_for(title, dif)
        target = int(g.target_value) if g.target_value is not None else 0
        remaining = ctx.records.remaining_to_target(note_count, current, target)
        items.append(
            {
                "goal_id": g.id, "song_title": title, "difficulty": dif, "target_score": target,
                "remaining_score": remaining[0] if remaining is not None else None,
                "remaining_attacks": remaining[1] if remaining is not None else None,
            }
        )
    items.sort(key=lambda i: (i["remaining_attacks"] is None, i["remaining_attacks"] or Decimal(0)))
    top = items[:_PLAN_LIMIT]

    def _plan_line(i: dict[str, Any]) -> str:
        remaining_text = (
            "계산 불가"
            if i["remaining_attacks"] is None
            else f"{i['remaining_attacks']}어택 ({i['remaining_score']:,}점)"
        )
        return f"{i['song_title']} {i['difficulty']}  {i['target_score']:,}점까지 {remaining_text}"

    lines = [_plan_line(i) for i in top]
    data = [
        {
            **i,
            "remaining_attacks": str(i["remaining_attacks"]) if i["remaining_attacks"] is not None else None,
        }
        for i in top
    ]
    return Section(order=_PLAN_ORDER, display=Display(content="\n".join(lines)), data={"kind": "goal_plan", "todos": data})
