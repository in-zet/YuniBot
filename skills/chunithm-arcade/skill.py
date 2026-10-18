# skills/chunithm-arcade/skill.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite
from chuni_penguin.adapters.errors import AuthenticationError, MaintenanceError

from core.decorators import on, tool
from core.events import Section
from core.tool_types import ArcadeRef, Display, ToolContext, ToolResult
from services.chunithm.arcade_settings import ArcadeSettings
from services.common.arcades import Arcade

_GAME = "chunithm"
_REPORT_ORDER = 50
_NO_ARCADE_HINT = "오락실을 말 안 했네. 다음 동기화 때 같이 말하거나 /오락실 방문으로 알려주면 설정을 저장해 둘게."

_FIELD_LABELS: dict[str, str] = {
    "speed": "스피드",
    "field_wall_position": "필드 월",
    "play_timing_offset": "판정 타이밍 A",
    "judge_timing_offset": "판정 타이밍 B",
    "guide_sound": "가이드음",
}


# ---------------------------------------------------------------- 공통 도우미


def _named_from_settings(settings: ArcadeSettings) -> dict[str, str]:
    return {
        "speed": settings.speed, "field_wall_position": settings.field_wall_position,
        "play_timing_offset": settings.play_timing_offset, "judge_timing_offset": settings.judge_timing_offset,
        "guide_sound": settings.guide_sound,
    }


def _named_from_row(row: aiosqlite.Row) -> dict[str, str]:
    return {name: row[name] for name in _FIELD_LABELS}


def _diff(target: dict[str, str], other: dict[str, str]) -> list[str]:
    return [label for name, label in _FIELD_LABELS.items() if target.get(name) != other.get(name)]


def _settings_line(named: dict[str, str]) -> str:
    return (
        f"스피드 {named['speed']} / 필드 월 {named['field_wall_position']} / "
        f"판정A {named['play_timing_offset']} / 판정B {named['judge_timing_offset']} / "
        f"가이드음 {named['guide_sound']}"
    )


async def _latest_settings(ctx: ToolContext, *, player_id: str, arcade_id: str | None) -> aiosqlite.Row | None:
    assert ctx.db is not None
    if arcade_id is not None:
        cursor = await ctx.db.execute(
            "SELECT * FROM arcade_settings WHERE player_id = ? AND arcade_id = ? ORDER BY read_at DESC LIMIT 1",
            (player_id, arcade_id),
        )
    else:
        cursor = await ctx.db.execute(
            "SELECT * FROM arcade_settings WHERE player_id = ? ORDER BY read_at DESC LIMIT 1",
            (player_id,),
        )
    return await cursor.fetchone()


async def _store_settings(
    ctx: ToolContext, *, visit_id: int, player_id: str, arcade_id: str, play_date: str, settings: ArcadeSettings
) -> None:
    assert ctx.db is not None
    named = _named_from_settings(settings)
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO arcade_settings (visit_id, player_id, arcade_id, play_date, speed, field_wall_position, "
        "play_timing_offset, judge_timing_offset, guide_sound, raw_json, read_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            visit_id, player_id, arcade_id, play_date, named["speed"], named["field_wall_position"],
            named["play_timing_offset"], named["judge_timing_offset"], named["guide_sound"],
            json.dumps(settings.raw), now,
        ),
    )
    await ctx.db.commit()


async def _last_visit_date(ctx: ToolContext, *, player_id: str, arcade_id: str) -> str | None:
    visits = await ctx.arcades.visits_for(player_id=player_id, game=_GAME)
    dates = [v.visited_at for v in visits if v.arcade_id == arcade_id]
    return max(dates) if dates else None


async def _apply_visit(ctx: ToolContext, arcade: Arcade, *, play_date: str | None) -> dict[str, Any]:
    """방문을 남기고, 그 날짜가 가장 최근 세션일 때만 CHUNITHM-NET의 지금 설정을 읽어
    저장한다. `record_visit` 도구와 `on_sync_reported`가 같이 쓰는 공유 로직이다."""
    latest = await ctx.records.latest_session(ctx.player_id)
    is_latest = play_date is None or latest is None or play_date == latest.play_date
    resolved_date = play_date or (latest.play_date if latest is not None else datetime.now(UTC).date().isoformat())

    visit_id = await ctx.arcades.record_visit(arcade.id, player_id=ctx.player_id, game=_GAME)

    if not is_latest:
        return {"play_date": resolved_date, "settings_saved": False, "reason": "historical", "changed": []}

    try:
        settings = await ctx.chunithm_net.get_arcade_settings(ctx.player_id, ctx=ctx)
    except (ConnectionError, MaintenanceError, AuthenticationError):
        return {"play_date": resolved_date, "settings_saved": False, "reason": "network_error", "changed": []}

    previous = await _latest_settings(ctx, player_id=ctx.player_id, arcade_id=arcade.id)
    changed = _diff(_named_from_row(previous), _named_from_settings(settings)) if previous is not None else []
    await _store_settings(
        ctx, visit_id=visit_id, player_id=ctx.player_id, arcade_id=arcade.id,
        play_date=resolved_date, settings=settings,
    )
    return {"play_date": resolved_date, "settings_saved": True, "reason": None, "changed": changed}


# ---------------------------------------------------------------- 도구


@tool(slash="오락실 설정")
async def get_settings(ctx: ToolContext, arcade: ArcadeRef) -> ToolResult:
    """그 오락실에서 마지막으로 저장한 설정과, 지금 계정 설정(가장 최근에 읽은 값)의 차이를 보여준다."""
    assert isinstance(arcade, Arcade)
    for_arcade = await _latest_settings(ctx, player_id=ctx.player_id, arcade_id=arcade.id)
    if for_arcade is None:
        last_visit = await _last_visit_date(ctx, player_id=ctx.player_id, arcade_id=arcade.id)
        if last_visit is None:
            return ToolResult(error=f"{arcade.name}에 방문한 기록이 없어.")
        return ToolResult(
            data={"arcade": arcade.name, "last_play_date": last_visit, "settings": None, "changed": []},
            display=Display(content=f"{arcade.name}의 설정은 아직 저장한 적 없어. 마지막 방문 {last_visit[:10]}."),
            display_mode="optional",
        )

    target = _named_from_row(for_arcade)
    current = await _latest_settings(ctx, player_id=ctx.player_id, arcade_id=None)
    changed = _diff(target, _named_from_row(current)) if current is not None else []

    lines = [f"{arcade.name}  마지막 방문 {for_arcade['play_date']}", _settings_line(target)]
    lines.append("지금 계정과 다른 항목: " + ", ".join(changed) if changed else "지금 계정 그대로 쓰면 돼.")
    return ToolResult(
        data={
            "arcade": arcade.name, "last_play_date": for_arcade["play_date"],
            "settings": target, "changed": changed,
        },
        display=Display(content="\n".join(lines)),
        display_mode="optional",
    )


@tool(slash="오락실 기록")
async def history(ctx: ToolContext, arcade: ArcadeRef) -> ToolResult:
    """그 오락실에서 저장된 설정 변화를 날짜순으로 보여준다."""
    assert isinstance(arcade, Arcade)
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT * FROM arcade_settings WHERE player_id = ? AND arcade_id = ? ORDER BY play_date",
        (ctx.player_id, arcade.id),
    )
    rows = await cursor.fetchall()
    if not rows:
        return ToolResult(error=f"{arcade.name}에 저장된 설정이 없어.")

    entries = [{"play_date": row["play_date"], **_named_from_row(row)} for row in rows]
    lines = [f"{e['play_date']}  {_settings_line(e)}" for e in entries]
    return ToolResult(
        data={"arcade": arcade.name, "entries": entries},
        display=Display(content=f"{arcade.name}\n" + "\n".join(lines)),
    )


@tool(slash="오락실 방문", tags=["chunithm-linked"], cost="network", mutates=True)
async def record_visit(ctx: ToolContext, arcade: ArcadeRef, date: str | None = None) -> ToolResult:
    """오락실 방문을 남긴다. date(ISO 날짜)를 안 주면 가장 최근 세션으로 본다 — 그때만
    CHUNITHM-NET의 지금 설정을 같이 읽어 저장한다. 지난 날짜는 그날 설정을 알 수 없어
    방문만 남는다."""
    assert isinstance(arcade, Arcade)
    outcome = await _apply_visit(ctx, arcade, play_date=date)

    lines = [f"{arcade.name} 방문을 남겼어. (플레이 날짜 {outcome['play_date']})"]
    if outcome["settings_saved"]:
        lines.append(
            "지금까지와 다른 항목: " + ", ".join(outcome["changed"])
            if outcome["changed"] else "지금까지와 달라진 건 없어."
        )
    elif outcome["reason"] == "historical":
        lines.append("지난 날짜라 그때 설정은 저장 안 했어.")
    else:
        lines.append("설정은 CHUNITHM-NET에서 못 읽었어.")

    return ToolResult(
        data={"arcade": arcade.name, **outcome},
        display=Display(content="\n".join(lines)),
        display_mode="optional",
    )


@tool()
async def set_privacy(ctx: ToolContext, public: bool) -> ToolResult:
    """오락실 방문 기록과 설정을 남에게 공개할지 정한다."""
    assert ctx.db is not None
    await ctx.db.execute(
        "INSERT INTO arcade_privacy (player_id, public) VALUES (?, ?) "
        "ON CONFLICT (player_id) DO UPDATE SET public = excluded.public",
        (ctx.player_id, int(public)),
    )
    await ctx.db.commit()
    content = "오락실 방문·설정을 이제부터 공개로 보여줄게." if public else "오락실 방문·설정을 비공개로 바꿨어."
    return ToolResult(data={"public": public}, display=Display(content=content), display_mode="optional")


# ---------------------------------------------------------------- 이벤트 핸들러


@on("chunithm.sync.completed")
async def on_sync_completed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """오락실을 안 말했을 때만 안내 문구를 한 줄 더한다(버튼 없음 — 동기화 리포트는
    `display.content`만 모아 보낸다)."""
    if not payload.get("has_new_plays") or payload.get("arcade_id") is not None:
        return None
    return Section(
        order=_REPORT_ORDER,
        display=Display(content=_NO_ARCADE_HINT),
        data={"kind": "arcade_prompt"},
    )


@on("chunithm.sync.reported")
async def on_sync_reported(ctx: ToolContext, payload: dict[str, Any]) -> None:
    """오락실을 동기화 때 말했으면(`arcade_id`) 방문을 남기고, 최근 세션이면 설정을
    읽어 저장한다. `chunithm-session`도 같은 이벤트를 듣지만 세션 리포트 저장만 하고
    방문 기록은 더 이상 하지 않는다(둘 다 하면 방문이 중복된다 — 구현 결정)."""
    if not payload.get("has_new_plays"):
        return
    arcade_id = payload.get("arcade_id")
    if arcade_id is None:
        return
    arcade = await ctx.arcades.get(arcade_id)
    if arcade is None:
        return

    sessions = payload.get("sessions") or []
    play_date = max((s["play_date"] for s in sessions), default=None)
    await _apply_visit(ctx, arcade, play_date=play_date)


@on("chunithm.session.viewed")
async def on_session_viewed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """그날 저장한 설정 조각. 본인이거나 `arcade_privacy`가 공개일 때만 돌려준다."""
    player_id: str = payload["player_id"]
    assert ctx.db is not None
    if player_id != ctx.player_id:
        privacy_cursor = await ctx.db.execute(
            "SELECT public FROM arcade_privacy WHERE player_id = ?", (player_id,)
        )
        privacy_row = await privacy_cursor.fetchone()
        if privacy_row is None or not privacy_row["public"]:
            return None

    cursor = await ctx.db.execute(
        "SELECT * FROM arcade_settings WHERE player_id = ? AND play_date = ? ORDER BY read_at DESC LIMIT 1",
        (player_id, payload["play_date"]),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    return Section(
        order=_REPORT_ORDER,
        display=Display(content=f"설정  {_settings_line(_named_from_row(row))}"),
        data={"kind": "arcade_settings", **_named_from_row(row)},
    )
