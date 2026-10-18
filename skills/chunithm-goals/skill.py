# skills/chunithm-goals/skill.py
from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.decorators import on, tool
from core.events import Section, Stage, highest_stage
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

_SKILL_NAME = "chunithm-goals"
_SYNCED_TAG = "chunithm-synced"
_SYNC_SECTION_ORDER = 30
_SONG_VIEW_ORDER = 10
_PLAN_ORDER = 10
_PLAN_LIMIT = 5

_SETTING_CHANNEL = "알림 채널"
_SETTING_HOUR = "알림 시각"
_DEFAULT_NOTIFY_TIME = time(10, 0)
_NOTIFY_DUE_EVENT = "chunithm-goals.notify_due"
_FINAL_DEADLINE_EVENT = "chunithm-goals.final_deadline"
_SCHEDULE_KIND_REMINDER = "reminder"
_SCHEDULE_KIND_FINAL = "final_deadline"
_FINAL_DEADLINE_DAYS = 4  # 기한 다음날 + 3일 대기(설계서 "기한 알림")


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


class SongLamp(StrEnum):
    """점수로 바꾸지 않는 곡 목표(램프). 값은 FolderCondition의 같은 이름과 같아 판정을 함께 쓴다."""

    FULL_COMBO = "full_combo"
    ALL_JUSTICE = "all_justice"


_LAMP_LABELS: dict[str, str] = {SongLamp.FULL_COMBO.value: "풀콤보", SongLamp.ALL_JUSTICE.value: "올 저스티스"}


class DurationTag(StrEnum):
    SHORT = "short"
    MID = "mid"
    LONG = "long"


class TodoSort(StrEnum):
    CONST = "const"
    REMAINING = "remaining"


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


def _previous_metric_value(deltas: RatingDeltas, metric: RatingMetric) -> Decimal | None:
    return {
        RatingMetric.COMPUTED: deltas.previous_computed_rating,
        RatingMetric.BEST30: deltas.previous_best30_average,
        RatingMetric.NEW20: deltas.previous_new20_average,
    }[metric]


def _default_notify_offsets(duration_tag: DurationTag | None) -> list[int]:
    offsets = [7, 1, 0]
    if duration_tag == DurationTag.LONG:
        offsets = [30, *offsets]
    return offsets


async def _guild_notify_time(ctx: ToolContext, guild_id: str | None) -> time:
    """그 서버의 "알림 시각" 설정(HH:MM). 서버가 없거나(DM에서 만든 목표) 설정이 이상하면 기본값."""
    if guild_id is None:
        return _DEFAULT_NOTIFY_TIME
    raw = await ctx.settings.get_value(
        scope="guild", scope_id=guild_id, skill=_SKILL_NAME, name=_SETTING_HOUR,
        default=_DEFAULT_NOTIFY_TIME.isoformat(timespec="minutes"),
    )
    try:
        return time.fromisoformat(raw)
    except (ValueError, TypeError):
        return _DEFAULT_NOTIFY_TIME


async def _cancel_schedules(ctx: ToolContext, goal_id: str, *, kind: str | None = None) -> None:
    """목표의 아직 안 쏜 예약(리마인더·최종 판정)을 취소한다. kind를 주면 그 종류만."""
    assert ctx.scheduler is not None
    for row in await models.list_schedules(ctx.db, goal_id, kind=kind):
        await ctx.scheduler.cancel_once(row.schedule_id)
    await models.delete_schedules(ctx.db, goal_id, kind=kind)


async def _reschedule_reminders(ctx: ToolContext, goal: Any) -> None:
    """목표의 리마인더 예약을 지우고 지금 값(기한·notify_offsets)으로 다시 건다. 기한이 없거나,
    진행 중이 아니거나, 알림을 꺼 뒀으면(notify_offsets가 빔) 다시 걸지 않는다. 이미 지난 시각은
    건너뛴다(만들 때 이미 7일 전이 지난 기한처럼)."""
    await _cancel_schedules(ctx, goal.id, kind=_SCHEDULE_KIND_REMINDER)
    if goal.due_at is None or goal.status != GoalStatus.IN_PROGRESS.value or not goal.notify_offsets:
        return
    due = date.fromisoformat(goal.due_at)
    notify_time = await _guild_notify_time(ctx, goal.guild_id)
    now = datetime.now()  # noqa: DTZ005 - 스케줄러·cron과 같은 서버 로컬 벽시계 기준(chunithm-updates의 _now()와 같은 이유)
    assert ctx.scheduler is not None
    for offset in goal.notify_offsets:
        fire_at = datetime.combine(due - timedelta(days=offset), notify_time)
        if fire_at <= now:
            continue
        schedule_id = await ctx.scheduler.schedule_once(
            owner_skill=_SKILL_NAME, event_name=_NOTIFY_DUE_EVENT,
            payload={"goal_id": goal.id, "offset_days": offset}, fire_at=fire_at, catch_up=True,
        )
        await models.insert_schedule(ctx.db, goal_id=goal.id, schedule_id=schedule_id, kind=_SCHEDULE_KIND_REMINDER)


async def _reschedule_final_deadline(ctx: ToolContext, goal: Any) -> None:
    """기한 다음 날부터 3일을 기다린 끝(기한+4일)의 최종 판정 예약을 다시 건다. 알림을 꺼 둬도
    이건 건다 — 달성 알림처럼 한 번뿐인 결과 통지라 "리마인더 끄기"의 대상이 아니라고 봤다."""
    await _cancel_schedules(ctx, goal.id, kind=_SCHEDULE_KIND_FINAL)
    if goal.due_at is None or goal.status != GoalStatus.IN_PROGRESS.value:
        return
    due = date.fromisoformat(goal.due_at)
    notify_time = await _guild_notify_time(ctx, goal.guild_id)
    fire_at = datetime.combine(due + timedelta(days=_FINAL_DEADLINE_DAYS), notify_time)
    assert ctx.scheduler is not None
    schedule_id = await ctx.scheduler.schedule_once(
        owner_skill=_SKILL_NAME, event_name=_FINAL_DEADLINE_EVENT,
        payload={"goal_id": goal.id}, fire_at=fire_at, catch_up=True,
    )
    await models.insert_schedule(ctx.db, goal_id=goal.id, schedule_id=schedule_id, kind=_SCHEDULE_KIND_FINAL)


async def _send_goal_notice(ctx: ToolContext, goal: Any, content: str) -> None:
    """목표의 guild_id 서버에 "알림 채널"이 있으면 멘션해서 그 채널로, 없으면(DM에서 만든
    목표 등) DM으로 보낸다."""
    channel_id = ""
    if goal.guild_id is not None:
        channel_id = await ctx.settings.get_value(
            scope="guild", scope_id=goal.guild_id, skill=_SKILL_NAME, name=_SETTING_CHANNEL, default="",
        )
    if channel_id:
        mentioned = await ctx.notify.mention(goal.player_id, content)
        await ctx.notify.post(channel_id, mentioned)
    else:
        await ctx.notify.dm(goal.player_id, content)


def _remaining_only_text(goal: Any, summary: dict[str, Any]) -> str:
    """기한 1일 전 알림용 — 남은 양만 짧게(설계서 "기한 알림")."""
    if summary.get("progress_hidden"):
        return _PROGRESS_HIDDEN
    if "lamp_met" in summary:
        return "이미 달성했어" if summary["lamp_met"] else "아직 달성 전이야"
    if goal.kind == GoalKind.FOLDER.value:
        return f"{summary.get('matched', 0)}/{summary.get('total', 0)}곡"
    if goal.kind == GoalKind.SONG.value:
        if summary.get("remaining_attacks") is None:
            return "남은 양 계산 불가"
        return f"남은 {summary['remaining_score']:,}점 ({summary['remaining_attacks']}어택)"
    if "remaining" in summary:
        return f"남은 {summary['remaining']}"
    return "진행 정보 없음"


async def _finalize_expired_goal(ctx: ToolContext, goal: Any) -> tuple[str, Stage]:
    """기한이 지나도 못 채운 목표를 만료로 확정한다(상태 변경 + 안 쏜 예약 전부 취소).
    (한 줄 요약, 반응 단계)를 돌려준다. 달성률 80% 이상이면 good, 진행이 없거나(0 이하)
    뒤처졌으면 rest, 그 사이는 close다 — 램프 목표처럼 달성률을 잴 수 없으면 close로 둔다."""
    summary = await _goal_summary(ctx, goal)
    ratio_raw = summary.get("achievement_ratio")
    stage: Stage
    if ratio_raw is None:
        stage = "close"
    else:
        ratio = Decimal(ratio_raw)
        if ratio >= Decimal("0.8"):
            stage = "good"
        elif ratio > 0:
            stage = "close"
        else:
            stage = "rest"
    await models.update_goal(ctx.db, goal.id, status=GoalStatus.EXPIRED.value)
    await _cancel_schedules(ctx, goal.id)
    detail = _summary_text(goal, summary)
    line = f"기한 만료  {_goal_label(goal)}{'  ' + detail if detail else ''}"
    return line, stage


_STATUS_LABELS: dict[str, str] = {
    GoalStatus.IN_PROGRESS.value: "진행 중",
    GoalStatus.ACHIEVED.value: "달성",
    GoalStatus.EXPIRED.value: "기한 만료",
    GoalStatus.STOPPED.value: "중단",
}
_PACE_LABELS: dict[str, str] = {"ahead": "앞섬", "on_track": "맞음", "behind": "뒤처짐"}
_NO_PREVIOUS = "비교할 직전 기록 없음"
_PROGRESS_HIDDEN = "진행 상황은 비공개야"


def _dec(value: float) -> Decimal:
    """DB(REAL)에서 읽은 값을 Decimal로. float을 그대로 넘기면 이진 오차가 따라오므로 문자열을 거친다."""
    return Decimal(str(value))


def _song_lamp(goal: Any) -> str | None:
    """곡 목표가 램프 목표면 그 램프("full_combo"/"all_justice"), 점수 목표면 None."""
    return goal.target_ref.get("lamp")


def _lamp_met(row: PbRow | None, lamp: str) -> bool:
    """램프 목표 판정. 폴더 조건과 같은 _row_matches_condition(combo_lamp 기준)을 쓴다."""
    return row is not None and _row_matches_condition(row, FolderCondition(lamp), None)


def _song_target_text(goal: Any) -> str:
    lamp = _song_lamp(goal)
    if lamp is not None:
        return _LAMP_LABELS.get(lamp, lamp)
    target = int(goal.target_value) if goal.target_value is not None else 0
    return f"{target:,}점"


def _goal_label(goal: Any) -> str:
    ref = goal.target_ref
    if goal.kind == "song":
        return f"{ref['song_title']} {ref['difficulty']}  {_song_target_text(goal)}"
    if goal.kind == "rating":
        target_text = calc.truncate2(_dec(goal.target_value)) if goal.target_value is not None else "?"
        return f"레이팅 {target_text}"
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
        condition = ref["condition"]
        if condition == FolderCondition.RANK.value and ref.get("rank"):
            condition = f"{ref['rank']} 이상"
        difficulty_label = ref["difficulty"] or "전체 난이도"
        return f"폴더({scope} {difficulty_label})  {condition}"
    return ref.get("description", "자유 목표")


def _parse_due(due_at: str | None) -> date | None | ToolResult:
    """기한 문자열(ISO 날짜, 예: 2026-12-31)을 검사한다. 비어 있으면 None(기한 없음)."""
    if due_at is None:
        return None
    try:
        return date.fromisoformat(due_at)
    except ValueError:
        return ToolResult(error="기한은 2026-12-31 같은 날짜로 알려 줘.")


def _parse_notify_offsets(text: str) -> list[int] | ToolResult:
    """쉼표로 구분한 "기한 며칠 전" 목록. 빈 문자열이면 알림을 끈다([])."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    try:
        offsets = [int(p) for p in parts]
    except ValueError:
        return ToolResult(error="알림 시점은 7,1,0처럼 기한 며칠 전인지 숫자로 알려 줘.")
    if any(o < 0 for o in offsets):
        return ToolResult(error="알림 시점은 0(당일) 이상이어야 해.")
    return offsets


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
    """폴더 정의(상수 범위/표기 레벨/수록 버전 중 하나 + 선택적 난이도)에 맞는 채보를
    songdb에서 찾는다. difficulty가 None이면 난이도를 가리지 않고 전부 찾는다(2026-10-18
    결정 — 상수·레벨은 채보 단위로 이미 정해지는 값이라 난이도로 좁힐 필요가 없다는
    실기 피드백. 버전은 애초에 songdb.find_by_version이 그렇게 지원했다)."""
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
        if (row := rows_by_key.get((hit.song.title, hit.chart.difficulty))) is not None
        and _row_matches_condition(row, condition, rank_min_score)
    )
    return total, matched


def _completed_payload(goal: Any) -> dict[str, Any]:
    """chunithm.goal.completed 페이로드: 플레이어, 목표, 종류, 등록일, 달성일."""
    return {
        "player_id": goal.player_id, "goal_id": goal.id, "kind": goal.kind, "label": _goal_label(goal),
        "created_at": goal.created_at, "completed_at": datetime.now(UTC).isoformat(),
    }


async def _complete_goal(ctx: ToolContext, goal: Any) -> str:
    """목표를 달성으로 바꾸고 chunithm.goal.completed를 낸다. 안 쏜 기한 알림 예약도 전부
    취소한다(기한 전 달성 — 설계서 "기한 알림"). 라벨을 돌려준다."""
    await models.update_goal(ctx.db, goal.id, status=GoalStatus.ACHIEVED.value)
    await _cancel_schedules(ctx, goal.id)
    payload = _completed_payload(goal)
    await ctx.emit("chunithm.goal.completed", payload)
    return payload["label"]


async def _record_progress_and_maybe_complete(
    ctx: ToolContext, goal: Any, sync_id: str | None, current: Decimal
) -> tuple[Decimal, str | None]:
    """진행 기록을 한 행 남기고, 이번에 처음 달성했으면 완료 처리 + 알림을 낸다. (달성률, 라벨)을
    돌려준다 — 라벨이 있으면 이번에 달성했다는 뜻이다(완료 목록에 쓴다)."""
    baseline = _dec(goal.baseline_value) if goal.baseline_value is not None else Decimal(0)
    target = _dec(goal.target_value) if goal.target_value is not None else current
    ratio = calc.achievement_ratio(current, baseline, target)
    await models.insert_progress(
        ctx.db, id=str(uuid.uuid4()), goal_id=goal.id, sync_id=sync_id,
        recorded_at=datetime.now(UTC).isoformat(), value=float(current), achievement_ratio=float(ratio),
    )
    if ratio >= 1:
        return ratio, await _complete_goal(ctx, goal)
    return ratio, None


async def _current_value(ctx: ToolContext, goal: Any) -> Decimal | None:
    """목표가 재는 값의 지금 값(마지막 동기화 기준). 자유 목표나 기록이 없으면 None."""
    if goal.kind == GoalKind.RATING.value:
        deltas = await ctx.records.rating_deltas(goal.player_id)
        return _metric_value(deltas, RatingMetric(goal.target_ref["metric"])) if deltas is not None else None
    if goal.kind == GoalKind.OVERPOWER.value:
        overpower = await ctx.records.latest_overpower(goal.player_id)
        if overpower is None:
            return None
        value, percentage = overpower
        current = percentage if goal.target_ref.get("mode") == OverpowerMode.PERCENTAGE.value else value
        return _dec(current) if current is not None else None
    if goal.kind == GoalKind.SONG.value:
        ref = goal.target_ref
        pb = await ctx.records.pb_row(goal.player_id, song_title=ref["song_title"], difficulty=ref["difficulty"])
        return Decimal(pb.score if pb is not None else 0)
    return None


async def _goal_summary(ctx: ToolContext, goal: Any) -> dict[str, Any]:
    """목표 하나의 지금 상태: 현재값, 달성률, 남은 양. 진행 기록(goals_progress)이 아직 없어도
    지금 기록으로 바로 계산한다(만든 직후의 레이팅 목표도 달성률이 나온다)."""
    summary: dict[str, Any] = {"current_value": None, "achievement_ratio": None}
    lamp = _song_lamp(goal) if goal.kind == GoalKind.SONG.value else None
    if lamp is not None:
        ref = goal.target_ref
        pb = await ctx.records.pb_row(goal.player_id, song_title=ref["song_title"], difficulty=ref["difficulty"])
        summary.update(current_lamp=pb.combo_lamp if pb is not None else None, lamp_met=_lamp_met(pb, lamp))
        return summary
    if goal.kind == GoalKind.FOLDER.value:
        total, matched = await _folder_counts(ctx, goal.target_ref, player_id=goal.player_id)
        ratio = calc.folder_progress(total, matched)
        summary.update(
            current_value=str(matched), total=total, matched=matched,
            achievement_ratio=str(ratio) if ratio is not None else None,
        )
        return summary

    current = await _current_value(ctx, goal)
    if current is None or goal.target_value is None:
        return summary
    target = _dec(goal.target_value)
    baseline = _dec(goal.baseline_value) if goal.baseline_value is not None else Decimal(0)
    summary["current_value"] = str(current)
    summary["achievement_ratio"] = str(calc.achievement_ratio(current, baseline, target))

    if goal.kind == GoalKind.RATING.value:
        remaining = calc.rating_remaining(current, target)
        summary["remaining"] = str(calc.truncate2(remaining))
        summary["song_rating_sum_needed"] = str(calc.truncate2(calc.rating_gap_to_song_rating_sum(remaining)))
    elif goal.kind == GoalKind.OVERPOWER.value:
        summary["remaining"] = str(calc.truncate2(calc.rating_remaining(current, target)))
    elif goal.kind == GoalKind.SONG.value:
        ref = goal.target_ref
        note_count = ctx.songdb.note_count_for(ref["song_title"], ref["difficulty"])
        remaining_pair = ctx.records.remaining_to_target(note_count, int(current), int(target))
        summary["remaining_score"] = remaining_pair[0] if remaining_pair is not None else None
        summary["remaining_attacks"] = str(remaining_pair[1]) if remaining_pair is not None else None
    return summary


async def _progress_visible(ctx: ToolContext, owner_id: str) -> bool:
    """남의 목표에서 진행값(현재값·달성률·남은 양·진행 기록·예상 레이팅)을 보여 줘도 되나.
    목표의 public은 목표 정의만 공개한다 — 진행값은 그 사람의 기록이라 records_public을 따른다
    (chunithm-report의 _target_player와 같은 규칙)."""
    if owner_id == ctx.player_id:
        return True
    owner = await ctx.players.get_player(owner_id)
    return owner is not None and owner.records_public


def _hidden_summary() -> dict[str, Any]:
    return {"current_value": None, "achievement_ratio": None, "progress_hidden": True}


def _summary_text(goal: Any, summary: dict[str, Any]) -> str:
    """요약을 한 줄로. 숫자는 표시용으로 소수 둘째 자리에서 버린다(data는 그대로)."""
    if summary.get("progress_hidden"):
        return _PROGRESS_HIDDEN
    if "current_lamp" in summary:
        return f"현재 램프 {summary['current_lamp'] or '기록 없음'}"
    parts: list[str] = []
    ratio = summary.get("achievement_ratio")
    if ratio is not None:
        parts.append(f"달성률 {calc.ratio_percent(Decimal(ratio))}%")
    current = summary.get("current_value")
    if goal.kind == GoalKind.FOLDER.value:
        parts.append(f"{summary.get('matched', 0)}/{summary.get('total', 0)}곡")
    elif goal.kind == GoalKind.SONG.value and current is not None:
        parts.append(f"현재 {int(Decimal(current)):,}")
        if summary.get("remaining_attacks") is None:
            parts.append("남은 양 계산 불가")
        else:
            parts.append(f"남은 {summary['remaining_score']:,}점 ({summary['remaining_attacks']}어택)")
    elif goal.kind in (GoalKind.RATING.value, GoalKind.OVERPOWER.value) and current is not None:
        parts.append(f"현재 {calc.truncate2(Decimal(current))}")
        remaining_text = f"남은 {summary['remaining']}"
        if goal.kind == GoalKind.RATING.value:
            remaining_text += f" (곡 레이팅 합 {summary['song_rating_sum_needed']})"
        parts.append(remaining_text)
    return " · ".join(parts)


async def _project_rating_for(ctx: ToolContext, player_id: str) -> Decimal | None:
    """곡별 목표(To-Do)를 모두 달성했을 때의 예상 레이팅(goals_calc.project_rating_with_todos).
    투두 채보가 이미 프레임에 있으면 그 항목을 바꾼다. 계산할 투두가 하나도 없으면 None이다."""
    best_rows, new_rows = await ctx.records.current_frames(player_id)
    best = [((r.song_title, r.difficulty), r.rating) for r in best_rows if r.rating is not None]
    new = [((r.song_title, r.difficulty), r.rating) for r in new_rows if r.rating is not None]
    song_goals = await models.list_goals_for_player(
        ctx.db, player_id, kind=GoalKind.SONG.value, statuses=[GoalStatus.IN_PROGRESS.value]
    )
    todo_targets: list[tuple[tuple[str, str], bool, Decimal]] = []
    for g in song_goals:
        title, dif = g.target_ref["song_title"], g.target_ref["difficulty"]
        const = ctx.songdb.const_for(title, dif)
        if const is None or g.target_value is None:
            continue
        rating = chart_rating(int(g.target_value), const)
        if rating is None:
            continue
        todo_targets.append(((title, dif), ctx.songdb.is_new_for(title), rating))
    if not todo_targets:
        return None
    return calc.project_rating_with_todos(best, new, todo_targets)


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
    lamp: SongLamp | None = None,
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
    현재값보다 커야 한다), folder는 (folder_min_const와 folder_max_const) 또는 folder_level 또는
    folder_version 중 하나 + 선택적 folder_difficulty(비우면 난이도를 가리지 않고 전부 묶는다)
    (+folder_condition, rank 조건이면 folder_rank), song은
    chart(곡+난이도 원문 그대로, 예: "크로노 마스")에 target_score(점수 숫자나 랭크 이름을 원문
    그대로 — 점수 환산은 해석기가 한다) 또는 lamp(full_combo/all_justice) 중 하나(이미 달성했으면
    에러), freeform은 description.
    due_at은 ISO 날짜(YYYY-MM-DD)다. 레이팅·오버파워·폴더 목표는 CHUNITHM-NET 동기화(chunithm-synced)가 있어야
    만들 수 있다. 레이팅 최종 목표는 플레이어당 하나뿐이다 — 이미 있으면 새로 만들지 않고
    에러를 돌려주고 `update`를 쓰라고 안내한다(설계서에 없던 세부, SKILL.md 본문 참고)."""
    # LLM 경로는 enum 인자를 원문 문자열로 넘긴다(core/schema.py) — 슬래시와 달리 여기서
    # 직접 enum으로 되돌려야 .value 호출이 안전하다(chunithm-report의 BestKind(kind)와 같은 패턴).
    kind = GoalKind(kind)
    metric = RatingMetric(metric)
    overpower_mode = OverpowerMode(overpower_mode)
    folder_condition = FolderCondition(folder_condition)
    if lamp is not None:
        lamp = SongLamp(lamp)
    if duration_tag is not None:
        duration_tag = DurationTag(duration_tag)
    if kind in (GoalKind.RATING, GoalKind.OVERPOWER, GoalKind.FOLDER) and _SYNCED_TAG not in ctx.tags:
        return ToolResult(error="그 목표는 CHUNITHM-NET 동기화가 돼 있어야 만들 수 있어. /동기화를 먼저 해 줘.")
    due = _parse_due(due_at)
    if isinstance(due, ToolResult):
        return due

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
        if _dec(target_value) <= baseline:
            return ToolResult(error=f"목표 레이팅은 지금 값({baseline})보다 높아야 해.")
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
        if _dec(target_value) <= _dec(current):
            return ToolResult(error=f"목표 오버파워는 지금 값({current})보다 높아야 해.")
        target_ref = {"mode": overpower_mode.value}
        baseline_value = float(current)
    elif kind == GoalKind.FOLDER:
        difficulty_code = _difficulty_code(folder_difficulty) if folder_difficulty is not None else None
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
        if chart is None:
            return ToolResult(error="곡과 난이도(chart)가 필요해.")
        if (score is None) == (lamp is None):
            return ToolResult(error="목표는 점수(target_score)나 램프(lamp: 풀콤보/올 저스티스) 중 하나만 줘.")
        assert isinstance(chart, ResolvedChart)
        song_title, difficulty = chart.song.title, chart.chart.difficulty
        if await models.find_song_goal(ctx.db, ctx.player_id, song_title=song_title, difficulty=difficulty):
            return ToolResult(error="그 채보에는 이미 진행 중인 투두가 있어.")
        row = await ctx.records.pb_row(ctx.player_id, song_title=song_title, difficulty=difficulty)
        if lamp is not None:
            # 램프 목표는 점수로 바꾸지 않는다 — target_ref에 램프를 싣고 target_value는 비운다.
            if _lamp_met(row, lamp.value):
                return ToolResult(error="이미 그 목표를 달성했어.")
            target_ref = {"song_title": song_title, "difficulty": difficulty, "lamp": lamp.value}
            target_value = None
            baseline_value = None
        else:
            assert score is not None
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

    if duration_tag is None and due is not None:
        # 꼬리표를 안 붙였으면 기한으로 정한다(2주 이내 단기, 3개월 이내 중기, 그 밖은 장기).
        duration_tag = DurationTag(calc.duration_tag_for(datetime.now(UTC).date(), due))
    await models.insert_goal(
        ctx.db, id=goal_id, player_id=ctx.player_id, kind=kind.value, target_ref=target_ref,
        target_value=target_value, baseline_value=baseline_value, created_at=now, due_at=due_at,
        duration_tag=duration_tag.value if duration_tag is not None else None, parent_goal_id=parent_goal_id,
        public=public, notify_offsets=_default_notify_offsets(duration_tag), guild_id=ctx.guild_id,
    )
    created_goal = await models.fetch_goal(ctx.db, goal_id)
    assert created_goal is not None
    await _reschedule_reminders(ctx, created_goal)
    await _reschedule_final_deadline(ctx, created_goal)
    label = _goal_label(created_goal)
    return ToolResult(
        data={"goal_id": goal_id, "kind": kind.value, "target_ref": target_ref, "target_value": target_value},
        display=Display(content=f"목표를 만들었어. ({label})\nid {goal_id}"),
        display_mode="optional",
    )


@tool(slash="목표 목록")
async def list_goals(ctx: ToolContext, player: PlayerRef | None = None, kind: GoalKind | None = None) -> ToolResult:
    """목표 목록과 달성률·남은 양을 보여준다. 각 목표의 id(수정/진행/삭제에 쓴다)도 함께
    보여준다. player를 주면 그 사람의 공개 목표만(본인이면 전체). 그 사람이 기록을 비공개로 해
    두었으면 목표 정의만 보이고 진행값은 빠진다."""
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    kind = GoalKind(kind) if kind is not None else None
    rows = await models.list_goals_for_player(ctx.db, target.id, kind=kind.value if kind is not None else None)
    if target.id != ctx.player_id:
        rows = [r for r in rows if r.public]  # 남의 목표는 목표마다 고른 공개 여부를 따른다

    # 목표의 public은 목표 정의만 공개한다. 진행값은 그 사람의 records_public을 따른다.
    show_progress = await _progress_visible(ctx, target.id)
    items: list[dict[str, Any]] = []
    lines: list[str] = []
    for r in rows:
        summary = await _goal_summary(ctx, r) if show_progress else _hidden_summary()
        items.append(
            {
                "goal_id": r.id, "kind": r.kind, "target_ref": r.target_ref, "target_value": r.target_value,
                "baseline_value": r.baseline_value if show_progress else None, "status": r.status, "due_at": r.due_at,
                "duration_tag": r.duration_tag, "public": r.public, **summary,
            }
        )
        status = _STATUS_LABELS.get(r.status, r.status)
        detail = _summary_text(r, summary)
        due = f"  기한 {r.due_at}" if r.due_at else ""
        lines.append(f"[{status}] {_goal_label(r)}{'  ' + detail if detail else ''}{due}\n  id {r.id}")
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
    target_score: ScoreRef | None = None,
    due_at: str | None = None,
    duration_tag: DurationTag | None = None,
    parent_goal_id: str | None = None,
    status: GoalStatus | None = None,
    public: bool | None = None,
    notify_offsets: str | None = None,
) -> ToolResult:
    """목표값·기한(YYYY-MM-DD)·기간 꼬리표·상위 목표·상태·공개 여부·알림 시점을 바꾼다(자유
    목표의 진행은 상태로 기록한다). 곡 목표의 점수는 target_score(점수 숫자나 랭크 이름을 원문
    그대로 — 점수 환산은 해석기가 한다)로 바꾼다. 레이팅·오버파워 목표는 target_value를 쓴다.
    notify_offsets는 기한 며칠 전에 알릴지 쉼표로(예: "7,1,0"), 빈 문자열이면 알림을 끈다.
    goal_id는 `/목표 목록`에 나오는 id다. 본인 목표만 바꿀 수 있다."""
    goal = await models.fetch_goal(ctx.db, goal_id)
    if goal is None or goal.player_id != ctx.player_id:
        return ToolResult(error="그 목표를 찾지 못했어.")

    if target_value is not None and target_score is not None:
        return ToolResult(error="점수(target_score)나 목표값(target_value) 중 하나만 줘.")
    if target_value is not None and goal.kind == GoalKind.SONG.value:
        # 곡 목표는 target_value를 받지 않는다(target_score만) — 이게 열려 있으면 모델이
        # 랭크 이름을 점수로 직접 계산해 target_value로 넘길 수 있다. 실기에서 "트리플(SSS,
        # 1,007,500)"을 1,000,000으로 잘못 계산해 넘긴 사례(CLAUDE.md "숫자는 코드가
        # 계산한다" 위반, progress/2026-10-18.md)가 target_score 추가 뒤에도 재현됐다 —
        # target_value가 여전히 받아졌기 때문이다. 이제 코드가 아예 거절한다.
        return ToolResult(error="곡 목표의 점수는 target_score로 줘(점수 숫자나 랭크 이름을 원문 그대로) — 점수를 직접 계산해서 넘기지 않는다.")
    if target_score is not None:
        if goal.kind != GoalKind.SONG.value:
            return ToolResult(error="target_score는 곡 목표에만 쓸 수 있어. 레이팅·오버파워는 target_value를 줘.")
        score = _score(target_score)
        assert score is not None
        target_value = float(score)

    duration_tag = DurationTag(duration_tag) if duration_tag is not None else None
    status = GoalStatus(status) if status is not None else None

    fields: dict[str, Any] = {}
    if target_value is not None:
        if (
            goal.kind in (GoalKind.RATING.value, GoalKind.OVERPOWER.value)
            and goal.baseline_value is not None
            and _dec(target_value) <= _dec(goal.baseline_value)
        ):
            return ToolResult(error=f"목표값은 설정할 때의 값({goal.baseline_value})보다 커야 해.")
        fields["target_value"] = target_value
    if due_at is not None:
        parsed = _parse_due(due_at)
        if isinstance(parsed, ToolResult):
            return parsed
        fields["due_at"] = due_at
    if duration_tag is not None:
        fields["duration_tag"] = duration_tag.value
    if parent_goal_id is not None:
        fields["parent_goal_id"] = parent_goal_id
    if status is not None:
        fields["status"] = status.value
    if public is not None:
        fields["public"] = public
    if notify_offsets is not None:
        offsets = _parse_notify_offsets(notify_offsets)
        if isinstance(offsets, ToolResult):
            return offsets
        fields["notify_offsets"] = offsets
    if not fields:
        return ToolResult(error="바꿀 내용이 없어.")

    await models.update_goal(ctx.db, goal_id, **fields)
    if {"due_at", "notify_offsets", "status"} & set(fields):
        # 기한·알림 시점·상태가 바뀌면 안 쏜 예약을 지금 값으로 다시 건다(중단·달성이면
        # 알아서 취소만 되고 새로 걸리지 않는다 — _reschedule_*의 status 확인).
        updated_goal = await models.fetch_goal(ctx.db, goal_id)
        assert updated_goal is not None
        await _reschedule_reminders(ctx, updated_goal)
        if "due_at" in fields or "status" in fields:
            await _reschedule_final_deadline(ctx, updated_goal)
    return ToolResult(
        data={"goal_id": goal_id, "updated": sorted(fields)},
        display=Display(content="목표를 수정했어."),
        display_mode="optional",
    )


@tool(slash="목표 진행")
async def progress(ctx: ToolContext, goal_id: str) -> ToolResult:
    """목표 하나의 지금 달성률·남은 양, 진행 기록과 페이스(기한이 있을 때)를 보여준다. 레이팅
    목표면 곡별 목표(To-Do)를 모두 달성했을 때의 예상 레이팅도 함께 보여준다. goal_id는
    `/목표 목록`에 나오는 id다. 남의 목표는 공개한 것만 볼 수 있고, 그 사람이 기록을
    비공개로 해 두었으면 진행값(현재값·달성률·남은 양·기록·예상 레이팅)은 빼고 보여준다."""
    goal = await models.fetch_goal(ctx.db, goal_id)
    if goal is None:
        return ToolResult(error="그 목표를 찾지 못했어.")
    if goal.player_id != ctx.player_id and not goal.public:
        return ToolResult(error="그 목표는 비공개야.")

    show_progress = await _progress_visible(ctx, goal.player_id)
    history = await models.list_progress(ctx.db, goal_id) if show_progress else []
    summary = await _goal_summary(ctx, goal) if show_progress else _hidden_summary()
    data: dict[str, Any] = {
        "goal_id": goal.id, "kind": goal.kind, "status": goal.status, "due_at": goal.due_at, **summary,
        "history": [
            {"sync_id": p.sync_id, "recorded_at": p.recorded_at, "value": p.value, "achievement_ratio": p.achievement_ratio}
            for p in history
        ],
        "pace": None, "elapsed_ratio": None,
    }
    lines = [f"[{_STATUS_LABELS.get(goal.status, goal.status)}] {_goal_label(goal)}"]
    detail = _summary_text(goal, summary)
    if detail:
        lines.append(detail)

    ratio = summary.get("achievement_ratio")
    due = _parse_due(goal.due_at)
    if ratio is not None and isinstance(due, date):
        created = datetime.fromisoformat(goal.created_at).date()
        elapsed = calc.elapsed_ratio(created, due, datetime.now(UTC).date())
        pace = calc.pace(Decimal(ratio), elapsed)
        data["pace"] = pace
        data["elapsed_ratio"] = str(elapsed)
        lines.append(f"기한 {goal.due_at} · 기간 경과 {calc.ratio_percent(elapsed)}% · 페이스 {_PACE_LABELS[pace]}")

    if history:
        lines.append(f"진행 기록 {len(history)}건 (마지막 {history[-1].recorded_at[:10]})")

    if goal.kind == GoalKind.RATING.value and show_progress:
        projected = await _project_rating_for(ctx, goal.player_id)
        data["projected_rating"] = str(projected) if projected is not None else None
        if projected is not None:
            lines.append(f"곡별 목표를 모두 달성하면 예상 레이팅 {calc.truncate2(projected)}")

    lines.append(f"id {goal.id}")
    return ToolResult(data=data, display=Display(content="\n".join(lines)), display_mode="optional")


@tool(slash="목표 삭제", mutates=True, confirm="bulk")
async def delete(ctx: ToolContext, goal_ids: str, *, dry_run: bool = False) -> ToolResult:
    """목표를 지운다(`/목표 목록`의 id, 쉼표로 여러 개 지정할 수 있다). 본인 목표만 지울 수
    있다. 2개 이상을 지울 때는 실행기가 지울 개수를 보여 주고 확인 버튼을 받은 뒤에 지운다."""
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
    for gid in owned:
        await _cancel_schedules(ctx, gid)
    count = await models.delete_goals(ctx.db, owned)
    return ToolResult(
        data={"deleted_count": count, "goal_ids": owned},
        display=Display(content=f"{count}개 지웠어."),
        display_mode="optional",
    )


@tool(slash="투두 추가", mutates=True)
async def todo_add(
    ctx: ToolContext, chart: ChartRef, target_score: ScoreRef | None = None, lamp: SongLamp | None = None,
    *, public: bool = False, due_at: str | None = None,
) -> ToolResult:
    """곡별 목표(To-Do) 한 건을 추가한다. chart는 곡+난이도 원문(예: "크로노 마스"). 목표는 둘 중
    하나만 준다: target_score(점수 숫자나 랭크 이름 SSS, SSS+ 등을 플레이어가 말한 그대로 — 점수
    환산은 해석기가 하므로 직접 계산하지 않는다) 또는 lamp(풀콤보면 full_combo, 올 저스티스면
    all_justice — 점수와 상관없는 램프 목표). 이미 달성한 채보면 에러다."""
    return await set_goal(
        ctx, GoalKind.SONG, chart=chart, target_score=target_score, lamp=lamp, public=public, due_at=due_at,
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
            guild_id=ctx.guild_id,
        )
        created.append(goal_id)

    return ToolResult(
        data={"count": len(created), "song_titles": targets, "goal_ids": created},
        display=Display(content=f"{len(created)}개 투두를 추가했어."),
        display_mode="required",
    )


@tool(slash="투두 목록")
async def todo_list(ctx: ToolContext, sort: TodoSort = TodoSort.CONST) -> ToolResult:
    """미완료 To-Do 목록을 상수순(const, 기본) 또는 남은 어택순(remaining)으로 보여준다. 각 투두의
    id(완료/삭제에 쓴다)도 함께 보여준다."""
    rows = await models.list_goals_for_player(
        ctx.db, ctx.player_id, kind=GoalKind.SONG.value, statuses=[GoalStatus.IN_PROGRESS.value]
    )
    items: list[dict[str, Any]] = []
    for g in rows:
        title, dif = g.target_ref["song_title"], g.target_ref["difficulty"]
        pb = await ctx.records.pb_row(ctx.player_id, song_title=title, difficulty=dif)
        current = pb.score if pb is not None else 0
        const = ctx.songdb.const_for(title, dif)
        lamp = _song_lamp(g)
        if lamp is not None:
            items.append(
                {
                    "goal_id": g.id, "song_title": title, "difficulty": dif, "const": const,
                    "current_score": current, "target_score": None, "lamp": lamp,
                    "current_lamp": pb.combo_lamp if pb is not None else None,
                    "remaining_score": None, "remaining_attacks": None,
                }
            )
            continue
        note_count = ctx.songdb.note_count_for(title, dif)
        target = int(g.target_value) if g.target_value is not None else 0
        remaining = ctx.records.remaining_to_target(note_count, current, target)
        items.append(
            {
                "goal_id": g.id, "song_title": title, "difficulty": dif, "const": const,
                "current_score": current, "target_score": target, "lamp": None,
                "remaining_score": remaining[0] if remaining is not None else None,
                "remaining_attacks": str(remaining[1]) if remaining is not None else None,
            }
        )

    if sort == TodoSort.REMAINING:
        items.sort(key=lambda i: (i["remaining_attacks"] is None, Decimal(i["remaining_attacks"] or "0")))
    else:
        items.sort(key=lambda i: (i["const"] is None, -(i["const"] or 0.0)))

    def _todo_line(i: dict[str, Any]) -> str:
        const_text = "" if i["const"] is None else f" {i['const']}"
        if i["lamp"] is not None:
            return (
                f"{i['song_title']} {i['difficulty']}{const_text}  {_LAMP_LABELS.get(i['lamp'], i['lamp'])} 목표  "
                f"(현재 램프 {i['current_lamp'] or '기록 없음'})\n  id {i['goal_id']}"
            )
        attacks_text = (
            "계산 불가" if i["remaining_attacks"] is None
            else f"남은 {i['remaining_score']:,}점, {i['remaining_attacks']}어택"
        )
        return (
            f"{i['song_title']} {i['difficulty']}{const_text}  "
            f"{i['current_score']:,} → {i['target_score']:,}  ({attacks_text})\n  id {i['goal_id']}"
        )

    lines = [_todo_line(i) for i in items]
    return ToolResult(
        data={"todos": items}, display=Display(content="\n".join(lines) if lines else "투두가 없어."),
        display_mode="required",
    )


@tool(slash="투두 완료", mutates=True)
async def todo_done(ctx: ToolContext, goal_id: str) -> ToolResult:
    """To-Do를 수동으로 완료 처리한다. goal_id는 `/투두 목록`에 나오는 id다."""
    goal = await models.fetch_goal(ctx.db, goal_id)
    if goal is None or goal.player_id != ctx.player_id or goal.kind != GoalKind.SONG.value:
        return ToolResult(error="그 투두를 찾지 못했어.")
    await _complete_goal(ctx, goal)
    return ToolResult(
        data={"goal_id": goal_id, "status": GoalStatus.ACHIEVED.value},
        display=Display(content="완료 처리했어."),
        display_mode="optional",
    )


# ---------------------------------------------------------------- 이벤트 핸들러


async def _rating_goal_block(
    ctx: ToolContext, goal: Any, sync_id: str | None, completed: list[str]
) -> tuple[list[str], dict[str, Any] | None]:
    """최종 목표 점검: 현재값, 남은 레이팅, 이번 동기화 전진량, 달성률(진행 기록도 남긴다)."""
    deltas = await ctx.records.rating_deltas(goal.player_id)
    if deltas is None:
        return [], None
    metric = RatingMetric(goal.target_ref["metric"])
    current = _metric_value(deltas, metric)
    if current is None or goal.target_value is None:
        return [], None
    ratio, label = await _record_progress_and_maybe_complete(ctx, goal, sync_id, current)
    target = _dec(goal.target_value)
    remaining = calc.truncate2(calc.rating_remaining(current, target))
    change = calc.value_change(current, _previous_metric_value(deltas, metric))
    change_text = _NO_PREVIOUS if change is None else f"이번 동기화 {change:+}"
    lines = [
        f"최종 목표 {calc.truncate2(target)}",
        (
            f"현재 {calc.truncate2(current)} · 남은 레이팅 {remaining} · {change_text} · "
            f"달성률 {calc.ratio_percent(ratio)}%"
        ),
    ]
    if label is not None:
        completed.append(label)
        lines.append(f"달성  {label}")
    data = {
        "goal_id": goal.id, "target": str(target), "current": str(current), "remaining": str(remaining),
        "change": str(change) if change is not None else None, "achievement_ratio": str(ratio),
    }
    return lines, data


@on("chunithm.sync.completed")
async def on_sync_completed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """동기화 리포트의 목표 점검 조각(order 30): 최종 목표(남은 레이팅, 이번 전진량, 달성률),
    곡별 목표(달성·접근·플레이했지만 미갱신)와 자동 완료, 오버파워·폴더 목표의 진행 기록(과
    달성 시 자동 완료). 진행 중인 목표가 하나도 없으면 None(조각을 내지 않는다)."""
    player_id: str = payload["player_id"]
    sync_id: str | None = payload.get("sync_id")
    active = await models.list_goals_for_player(ctx.db, player_id, statuses=[GoalStatus.IN_PROGRESS.value])
    if not active:
        return None

    diff = await ctx.records.sync_diff(sync_id) if sync_id else None
    update_by_key = {(u.song_title, u.difficulty): u for u in (diff.chart_updates if diff is not None else [])}
    unchanged_by_key = {(u.song_title, u.difficulty): u for u in (diff.unchanged_plays if diff is not None else [])}

    completed: list[str] = []
    completed_ids: set[str] = set()
    blocks: list[list[str]] = []
    rating_data: dict[str, Any] | None = None

    rating_goal = next((g for g in active if g.kind == GoalKind.RATING.value), None)
    if rating_goal is not None:
        before = len(completed)
        rating_lines, rating_data = await _rating_goal_block(ctx, rating_goal, sync_id, completed)
        if len(completed) > before:
            completed_ids.add(rating_goal.id)
        if rating_lines:
            blocks.append(rating_lines)

    song_lines: list[str] = []
    approached: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for g in (g for g in active if g.kind == GoalKind.SONG.value):
        title, dif = g.target_ref["song_title"], g.target_ref["difficulty"]
        chart_update = update_by_key.get((title, dif))
        unchanged_play = unchanged_by_key.get((title, dif))
        lamp = _song_lamp(g)
        if lamp is not None:
            # 램프 목표: 동기화 diff(ChartUpdate)에는 램프가 없으므로, 동기화가 갱신한 records_pb의
            # combo_lamp로 판정한다(점수 비교는 쓰지 않는다).
            pb = await ctx.records.pb_row(player_id, song_title=title, difficulty=dif)
            lamp_text = _LAMP_LABELS.get(lamp, lamp)
            if _lamp_met(pb, lamp):
                completed.append(await _complete_goal(ctx, g))
                completed_ids.add(g.id)
                song_lines.append(f"달성  {title} {dif}  {lamp_text}")
            elif chart_update is not None or unchanged_play is not None:
                song_lines.append(
                    f"미갱신  {title} {dif}  {lamp_text} 아직 (현재 램프 {pb.combo_lamp if pb is not None else '기록 없음'})"
                )
            continue
        target = int(g.target_value) if g.target_value is not None else 0
        if chart_update is not None and chart_update.new_score >= target:
            completed.append(await _complete_goal(ctx, g))
            completed_ids.add(g.id)
            song_lines.append(f"달성  {title} {dif}  {target:,}점  (현재 {chart_update.new_score:,})")
        elif chart_update is not None:
            current = chart_update.new_score
            note_count = ctx.songdb.note_count_for(title, dif)
            remaining = ctx.records.remaining_to_target(note_count, current, target)
            if remaining is None:
                song_lines.append(f"접근  {title} {dif}  {target:,}점까지 남은 양 계산 불가 (현재 {current:,})")
            else:
                song_lines.append(
                    f"접근  {title} {dif}  {target:,}점까지 {remaining[0]:,}점 ({remaining[1]}어택, 현재 {current:,})"
                )
            approached.append(
                {
                    "goal_id": g.id, "song_title": title, "difficulty": dif, "target_score": target,
                    "current_score": current, "remaining_score": remaining[0] if remaining is not None else None,
                    "remaining_attacks": str(remaining[1]) if remaining is not None else None,
                }
            )
        elif unchanged_play is not None:
            song_lines.append(
                f"미갱신  {title} {dif}  플레이 {unchanged_play.play_count}회, 최고 기록 유지 "
                f"(현재 {unchanged_play.current_score:,})"
            )
            unchanged.append(
                {
                    "goal_id": g.id, "song_title": title, "difficulty": dif, "target_score": target,
                    "play_count": unchanged_play.play_count, "current_score": unchanged_play.current_score,
                }
            )
    if song_lines:
        blocks.append(["곡별 목표", *song_lines])

    other_lines: list[str] = []
    for g in (g for g in active if g.kind == GoalKind.OVERPOWER.value):
        current_value = await _current_value(ctx, g)
        if current_value is None:
            continue
        _ratio, label = await _record_progress_and_maybe_complete(ctx, g, sync_id, current_value)
        if label is not None:
            completed.append(label)
            completed_ids.add(g.id)
            other_lines.append(f"달성  {label}")

    for g in (g for g in active if g.kind == GoalKind.FOLDER.value):
        total, matched = await _folder_counts(ctx, g.target_ref, player_id=player_id)
        ratio = calc.folder_progress(total, matched)
        await models.insert_progress(
            ctx.db, id=str(uuid.uuid4()), goal_id=g.id, sync_id=sync_id,
            recorded_at=datetime.now(UTC).isoformat(), value=float(matched),
            achievement_ratio=float(ratio) if ratio is not None else None,
        )
        if ratio is not None and ratio >= 1:
            label = await _complete_goal(ctx, g)
            completed.append(label)
            completed_ids.add(g.id)
            other_lines.append(f"달성  {label}")
    if other_lines:
        blocks.append(other_lines)

    today = datetime.now(UTC).date()
    expired_lines: list[str] = []
    expired: list[dict[str, Any]] = []
    for g in active:
        if g.id in completed_ids or g.due_at is None or date.fromisoformat(g.due_at) >= today:
            continue
        line, stage = await _finalize_expired_goal(ctx, g)
        expired_lines.append(line)
        expired.append({"goal_id": g.id, "stage": stage})
    if expired_lines:
        blocks.append(expired_lines)

    content = "\n\n".join("\n".join(b) for b in blocks) if blocks else "목표 변화 없음"
    stages: list[Stage] = [*(["great"] if completed else []), *(e["stage"] for e in expired)]
    return Section(
        order=_SYNC_SECTION_ORDER,
        display=Display(content=content),
        data={
            "kind": "goal_check", "completed": completed, "rating_goal": rating_data,
            "approached": approached, "unchanged": unchanged, "expired": expired,
        },
        stage=highest_stage(stages),
    )


@on(_NOTIFY_DUE_EVENT)
async def on_notify_due(ctx: ToolContext, payload: dict[str, Any]) -> None:
    """예약된 기한 리마인더(30/7/1/0일 전)가 도착했다. 그 사이 끝난(달성·중단) 목표면 조용히
    넘어간다 — 중복 발송 방지는 goal.status만 본다(상태 변경 자리에서 이미 예약을 취소한다)."""
    goal_id = payload.get("goal_id")
    offset = payload.get("offset_days")
    if goal_id is None or offset is None:
        return
    goal = await models.fetch_goal(ctx.db, goal_id)
    if goal is None or goal.status != GoalStatus.IN_PROGRESS.value:
        return
    offset = int(offset)
    summary = await _goal_summary(ctx, goal)
    label = _goal_label(goal)
    if offset == 0:
        detail = _summary_text(goal, summary)
        content = f"오늘이 '{label}' 기한이야! 플레이하고 동기화해 줘."
        if detail:
            content += f"\n{detail}"
    elif offset == 1:
        content = f"'{label}' 기한이 1일 남았어. {_remaining_only_text(goal, summary)}"
    else:
        detail = _summary_text(goal, summary)
        pace_text = ""
        due = _parse_due(goal.due_at)
        ratio_raw = summary.get("achievement_ratio")
        if isinstance(due, date) and ratio_raw is not None:
            created = datetime.fromisoformat(goal.created_at).date()
            elapsed = calc.elapsed_ratio(created, due, datetime.now(UTC).date())
            pace_text = f" · 페이스 {_PACE_LABELS[calc.pace(Decimal(ratio_raw), elapsed)]}"
        content = f"'{label}' 기한이 {offset}일 남았어. {detail}{pace_text}"
    await _send_goal_notice(ctx, goal, content)


@on(_FINAL_DEADLINE_EVENT)
async def on_final_deadline(ctx: ToolContext, payload: dict[str, Any]) -> None:
    """기한+4일(다음 날부터 3일 대기)까지도 동기화로 끝나지 않은 목표를 마지막으로 아는
    값으로 판정한다. 그 사이 동기화로 이미 끝났으면(달성·만료) 아무 일도 하지 않는다."""
    goal_id = payload.get("goal_id")
    if goal_id is None:
        return
    goal = await models.fetch_goal(ctx.db, goal_id)
    if goal is None or goal.status != GoalStatus.IN_PROGRESS.value:
        return
    line, stage = await _finalize_expired_goal(ctx, goal)
    yuni_line = await ctx.yuni.line([Section(order=0, stage=stage)])
    await _send_goal_notice(ctx, goal, f"{line}\n{yuni_line}")


@on("chunithm.song.viewed")
async def on_song_viewed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """그 곡에 내 To-Do가 있으면 상태와 남은 어택을 붙인다. difficulty가 None이면(곡 전체 보기)
    그 곡의 모든 난이도 투두를, 난이도 코드면(채보 보기) 그 난이도 투두만 보여준다. 없으면 None."""
    player_id: str = payload["player_id"]
    title: str = payload["song_title"]
    dif: str | None = payload.get("difficulty") or None
    goals = [
        g
        for g in await models.list_goals_for_player(
            ctx.db, player_id, kind=GoalKind.SONG.value, statuses=[GoalStatus.IN_PROGRESS.value]
        )
        if g.target_ref.get("song_title") == title and (dif is None or g.target_ref.get("difficulty") == dif)
    ]
    if not goals:
        return None

    todos: list[dict[str, Any]] = []
    lines: list[str] = []
    for goal in goals:
        goal_dif: str = goal.target_ref["difficulty"]
        pb = await ctx.records.pb_row(player_id, song_title=title, difficulty=goal_dif)
        current = pb.score if pb is not None else 0
        lamp = _song_lamp(goal)
        if lamp is not None:
            current_lamp = pb.combo_lamp if pb is not None else None
            lines.append(
                f"투두 {goal_dif} 목표 {_LAMP_LABELS.get(lamp, lamp)}  (현재 램프 {current_lamp or '기록 없음'})"
            )
            todos.append(
                {
                    "goal_id": goal.id, "difficulty": goal_dif, "current_score": current, "target_score": None,
                    "lamp": lamp, "current_lamp": current_lamp, "remaining_score": None, "remaining_attacks": None,
                }
            )
            continue
        note_count = ctx.songdb.note_count_for(title, goal_dif)
        target = int(goal.target_value) if goal.target_value is not None else 0
        remaining = ctx.records.remaining_to_target(note_count, current, target)
        if remaining is None:
            lines.append(f"투두 {goal_dif} 목표 {target:,}점  (현재 {current:,}, 남은 양 계산 불가)")
        else:
            lines.append(
                f"투두 {goal_dif} 목표 {target:,}점  남은 {remaining[0]:,}점 ({remaining[1]}어택, 현재 {current:,})"
            )
        todos.append(
            {
                "goal_id": goal.id, "difficulty": goal_dif, "current_score": current, "target_score": target,
                "remaining_score": remaining[0] if remaining is not None else None,
                "remaining_attacks": str(remaining[1]) if remaining is not None else None,
            }
        )
    return Section(
        order=_SONG_VIEW_ORDER,
        display=Display(content="\n".join(lines)),
        data={"kind": "goal_todo", "difficulty": dif, "todos": todos},
    )


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
        lamp = _song_lamp(g)
        if lamp is not None:
            # 램프 목표는 남은 어택으로 잴 수 없어 맨 뒤로 간다.
            items.append(
                {
                    "goal_id": g.id, "song_title": title, "difficulty": dif, "target_score": None, "lamp": lamp,
                    "remaining_score": None, "remaining_attacks": None,
                }
            )
            continue
        note_count = ctx.songdb.note_count_for(title, dif)
        target = int(g.target_value) if g.target_value is not None else 0
        remaining = ctx.records.remaining_to_target(note_count, current, target)
        items.append(
            {
                "goal_id": g.id, "song_title": title, "difficulty": dif, "target_score": target, "lamp": None,
                "remaining_score": remaining[0] if remaining is not None else None,
                "remaining_attacks": remaining[1] if remaining is not None else None,
            }
        )
    items.sort(key=lambda i: (i["remaining_attacks"] is None, i["remaining_attacks"] or Decimal(0)))
    top = items[:_PLAN_LIMIT]

    def _plan_line(i: dict[str, Any]) -> str:
        if i["lamp"] is not None:
            return f"{i['song_title']} {i['difficulty']}  {_LAMP_LABELS.get(i['lamp'], i['lamp'])} 목표"
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
