# skills/chunithm-session/skill.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from core.decorators import on, tool
from core.tool_types import ArcadeRef, Display, PlayerRef, ToolContext, ToolResult
from services.common.arcades import Arcade
from services.common.players import Player

_GAME = "chunithm"
_SYNC_REPORT_KIND = "sync_report"
_UNKNOWN = "계산 불가"
_PRIVATE_ERROR = "그 사람은 기록을 비공개로 해 놨어."
_NOT_SYNCED_ERROR = "아직 저장된 세션이 없어."


# ---------------------------------------------------------------- 공통 도우미


async def _target_player(ctx: ToolContext, player: PlayerRef | None) -> Player | ToolResult:
    """조회 대상 플레이어(chunithm-report._target_player와 같은 규칙). 본인은 공개 설정과
    무관하게 보고, 남은 records_public이 켜져 있을 때만 본다."""
    if player is None:
        me = await ctx.players.get_player(ctx.player_id)
        if me is None:
            return ToolResult(error="플레이어 정보를 찾지 못했어.")
        return me
    assert isinstance(player, Player)
    if player.id != ctx.player_id and not player.records_public:
        return ToolResult(error=_PRIVATE_ERROR)
    return player


async def _all_report_rows(ctx: ToolContext, *, player_id: str) -> list[aiosqlite.Row]:
    assert ctx.db is not None
    cursor = await ctx.db.execute(
        "SELECT session_id, play_date, estimated, rendered_text, sections_data, yuni_line "
        "FROM session_reports WHERE player_id = ? AND game = ? ORDER BY play_date DESC",
        (player_id, _GAME),
    )
    return list(await cursor.fetchall())


async def _visits_by_date(ctx: ToolContext, *, player_id: str) -> dict[str, str]:
    """플레이 날짜(visited_at의 날짜 부분) → 오락실 id. 하루에 방문이 여러 번이면 가장 나중
    것이 이긴다(visits_for는 visited_at 오름차순). visited_at은 동기화 시각(UTC) 기준이라
    플레이 날짜(새벽 4시 경계, 로컬)와 어긋날 수 있다 — 오락실 스킬이 생기기 전까지의
    근사값이다."""
    visits = await ctx.arcades.visits_for(player_id=player_id, game=_GAME)
    return {v.visited_at[:10]: v.arcade_id for v in visits}


async def _visited_arcade_names(ctx: ToolContext, *, player_id: str) -> list[str]:
    visits = await _visits_by_date(ctx, player_id=player_id)
    names: list[str] = []
    seen: set[str] = set()
    for arcade_id in visits.values():
        if arcade_id in seen:
            continue
        seen.add(arcade_id)
        found = await ctx.arcades.get(arcade_id)
        if found is not None:
            names.append(found.name)
    return names


def _sync_section(sections_data: list[dict[str, Any]]) -> dict[str, Any] | None:
    for section in sections_data:
        if section.get("kind") == _SYNC_REPORT_KIND:
            return section
    return None


def _updated_count_for(sections_data: list[dict[str, Any]], session_id: str) -> int:
    sync_section = _sync_section(sections_data)
    if sync_section is None:
        return 0
    for s in sync_section.get("sessions", []):
        if s.get("id") == session_id:
            count: int = s.get("updated_count", 0)
            return count
    return 0


async def _changes_block(
    ctx: ToolContext, *, player_id: str, session_id: str, sections_data: list[dict[str, Any]]
) -> tuple[str, dict[str, Any]]:
    """저장된 리포트 시점의 채보·레이팅과 지금 값을 나란히 비교한다(design "그 뒤의 변화")."""
    sync_section = _sync_section(sections_data)
    summary = await ctx.records.latest_sync_summary(player_id)
    now_rating = str(summary.computed_rating) if summary and summary.computed_rating is not None else _UNKNOWN
    then_rating = sync_section.get("current", {}).get("computed_rating") if sync_section else None

    lines: list[str] = []
    chart_changes: list[dict[str, Any]] = []
    for u in (sync_section.get("chart_updates", []) if sync_section else []):
        if u.get("session_id") != session_id:
            continue
        current = await ctx.records.pb_row(player_id, song_title=u["song_title"], difficulty=u["difficulty"])
        if current is None:
            continue
        current_rating = str(current.rating) if current.rating is not None else _UNKNOWN
        if current.rank == u["new_rank"] and current.score == u["new_score"]:
            continue
        chart_changes.append(
            {
                "song_title": u["song_title"], "difficulty": u["difficulty"],
                "then_rank": u["new_rank"], "now_rank": current.rank,
                "then_score": u["new_score"], "now_score": current.score,
                "then_rating": u["new_rating"], "now_rating": current_rating,
            }
        )
        lines.append(f"{u['song_title']} {u['difficulty']}  그때 {u['new_rank']} → 지금 {current.rank}")

    if then_rating is not None and then_rating != now_rating:
        lines.insert(0, f"레이팅  그때 {then_rating} → 지금 {now_rating}")

    content = "\n".join(lines) if lines else "그때와 달라진 게 없어."
    data = {"rating_then": then_rating, "rating_now": now_rating, "chart_changes": chart_changes}
    return content, data


# ---------------------------------------------------------------- 도구


@tool(slash="세션 목록")
async def list_sessions(
    ctx: ToolContext,
    player: PlayerRef | None = None,
    start: str | None = None,
    end: str | None = None,
    arcade: ArcadeRef | None = None,
) -> ToolResult:
    """세션(플레이 날짜) 목록을 날짜순으로 보여준다. start/end(ISO 날짜, YYYY-MM-DD)로 기간을 좁히고, arcade로 그 오락실에서 플레이한 세션만 본다. player를 비우면 본인이다."""
    assert arcade is None or isinstance(arcade, Arcade)
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target
    assert ctx.db is not None

    conditions = ["player_id = ?", "game = ?"]
    params: list[Any] = [target.id, _GAME]
    if start is not None:
        conditions.append("play_date >= ?")
        params.append(start)
    if end is not None:
        conditions.append("play_date <= ?")
        params.append(end)
    cursor = await ctx.db.execute(
        f"SELECT session_id, play_date, estimated, sections_data FROM session_reports "
        f"WHERE {' AND '.join(conditions)} ORDER BY play_date",
        params,
    )
    rows: list[aiosqlite.Row] = list(await cursor.fetchall())

    visits = await _visits_by_date(ctx, player_id=target.id)
    if arcade is not None:
        rows = [r for r in rows if visits.get(r["play_date"]) == arcade.id]
    if not rows:
        return ToolResult(
            data={"player_id": target.id, "sessions": []}, display=Display(content="그 조건엔 세션이 없어.")
        )

    arcade_cache: dict[str, str | None] = {}
    items: list[dict[str, Any]] = []
    lines: list[str] = []
    for r in rows:
        play_date = r["play_date"]
        updated_count = _updated_count_for(json.loads(r["sections_data"]), r["session_id"])
        arcade_id = visits.get(play_date)
        arcade_name = None
        if arcade_id is not None:
            if arcade_id not in arcade_cache:
                found = await ctx.arcades.get(arcade_id)
                arcade_cache[arcade_id] = found.name if found is not None else None
            arcade_name = arcade_cache[arcade_id]
        items.append(
            {
                "session_id": r["session_id"], "play_date": play_date, "estimated": bool(r["estimated"]),
                "arcade": arcade_name, "updated_count": updated_count,
            }
        )
        suffix = " (추정)" if r["estimated"] else ""
        arcade_part = f"  {arcade_name}" if arcade_name else ""
        lines.append(f"{play_date}{suffix}{arcade_part}  갱신 {updated_count}채보")

    return ToolResult(data={"player_id": target.id, "sessions": items}, display=Display(content="\n".join(lines)))


@tool(slash="세션 보기")
async def get_session(
    ctx: ToolContext,
    player: PlayerRef | None = None,
    date: str | None = None,
    arcade: ArcadeRef | None = None,
) -> ToolResult:
    """세션 하나를 저장된 리포트 그대로 불러오고, 그 뒤 바뀐 채보·레이팅을 지금 값과 나란히 보여준다. date(ISO 날짜, YYYY-MM-DD)를 주면 그 날짜, 안 주면 가장 최근이다. arcade를 주면 그 오락실에서의 세션만 찾는다. player를 비우면 본인이다."""
    assert arcade is None or isinstance(arcade, Arcade)
    target = await _target_player(ctx, player)
    if isinstance(target, ToolResult):
        return target

    rows = await _all_report_rows(ctx, player_id=target.id)
    if not rows:
        return ToolResult(error=_NOT_SYNCED_ERROR)

    if arcade is not None:
        visits = await _visits_by_date(ctx, player_id=target.id)
        rows = [r for r in rows if visits.get(r["play_date"]) == arcade.id]
        if not rows:
            names = await _visited_arcade_names(ctx, player_id=target.id)
            return ToolResult(
                error="그 오락실에서의 세션이 없어.", data={"player_id": target.id, "visited_arcades": names}
            )

    if date is not None:
        row = next((r for r in rows if r["play_date"] == date), None)
        if row is None:
            dates = sorted(r["play_date"] for r in rows)
            before = max((d for d in dates if d < date), default=None)
            after = min((d for d in dates if d > date), default=None)
            return ToolResult(
                error="그 날짜엔 세션이 없어.",
                data={"player_id": target.id, "nearby": {"before": before, "after": after}},
            )
    else:
        row = rows[0]  # _all_report_rows는 play_date 내림차순이라 맨 앞이 최신이다

    sections_data = json.loads(row["sections_data"])
    changes_content, changes_data = await _changes_block(
        ctx, player_id=target.id, session_id=row["session_id"], sections_data=sections_data
    )

    visits = await _visits_by_date(ctx, player_id=target.id)
    arcade_id = visits.get(row["play_date"])
    arcade_name = None
    if arcade_id is not None:
        found = await ctx.arcades.get(arcade_id)
        arcade_name = found.name if found is not None else None

    sections = await ctx.emit_gather(
        "chunithm.session.viewed",
        {"player_id": target.id, "session_id": row["session_id"], "play_date": row["play_date"]},
    )
    extra_lines = [s.display.content for s in sections if s.display is not None and s.display.content]

    blocks = [f"[저장된 리포트  {row['play_date']}]", row["rendered_text"]]
    extra_blocks = ([f"오락실  {arcade_name}"] if arcade_name else []) + extra_lines
    if extra_blocks:
        blocks.append("\n".join(extra_blocks))
    blocks.append(f"[그 뒤의 변화]\n{changes_content}")

    return ToolResult(
        data={
            "player_id": target.id, "session_id": row["session_id"], "play_date": row["play_date"],
            "estimated": bool(row["estimated"]), "rendered_text": row["rendered_text"],
            "yuni_line": row["yuni_line"], "arcade": arcade_name, "changes": changes_data,
        },
        display=Display(content="\n\n".join(blocks)),
    )


@tool(slash="세션 삭제", mutates=True, confirm="always")
async def delete_session(
    ctx: ToolContext, date: str | None = None, start: str | None = None, end: str | None = None
) -> ToolResult:
    """내 세션 요약을 지운다(실제 플레이 기록은 지우지 않는다). date 하나 또는 start/end 기간을 줘야 한다."""
    assert ctx.db is not None
    if date is None and start is None and end is None:
        return ToolResult(error="지울 날짜나 기간을 알려줘.")

    conditions = ["player_id = ?", "game = ?"]
    params: list[Any] = [ctx.player_id, _GAME]
    if date is not None:
        conditions.append("play_date = ?")
        params.append(date)
    else:
        if start is not None:
            conditions.append("play_date >= ?")
            params.append(start)
        if end is not None:
            conditions.append("play_date <= ?")
            params.append(end)

    cursor = await ctx.db.execute(f"DELETE FROM session_reports WHERE {' AND '.join(conditions)}", params)
    await ctx.db.commit()
    if cursor.rowcount == 0:
        return ToolResult(error="그 조건에 맞는 세션이 없어.")
    return ToolResult(
        data={"deleted_count": cursor.rowcount},
        display=Display(content=f"세션 {cursor.rowcount}개를 지웠어."),
        display_mode="optional",
    )


# ---------------------------------------------------------------- 이벤트 핸들러


@on("chunithm.sync.reported")
async def on_sync_reported(ctx: ToolContext, payload: dict[str, Any]) -> None:
    """리포트를 세션별로 고정해 저장한다. 새 플레이가 없는 동기화는 저장하지 않는다. 같은
    세션(날짜)에 다시 동기화하면 세션당 저장본이 하나뿐이라 최신 리포트로 바뀐다."""
    if not payload.get("has_new_plays"):
        return
    assert ctx.db is not None
    player_id: str = payload["player_id"]
    rendered_text: str = payload["rendered_text"]
    sections_json = json.dumps(payload["sections_data"])
    yuni_line: str = payload.get("yuni_line") or ""
    now = datetime.now(UTC).isoformat()

    for session in payload.get("sessions", []):
        await ctx.db.execute(
            "INSERT INTO session_reports (session_id, player_id, game, play_date, estimated, "
            "rendered_text, sections_data, yuni_line, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (session_id) DO UPDATE SET play_date = excluded.play_date, "
            "estimated = excluded.estimated, rendered_text = excluded.rendered_text, "
            "sections_data = excluded.sections_data, yuni_line = excluded.yuni_line, "
            "created_at = excluded.created_at",
            (
                session["id"], player_id, _GAME, session["play_date"], int(session["estimated"]),
                rendered_text, sections_json, yuni_line, now,
            ),
        )

    await ctx.db.commit()
