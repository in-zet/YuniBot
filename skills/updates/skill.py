# skills/updates/skill.py
from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
from datetime import UTC, datetime, time
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.decorators import on, schedule, tool
from core.events import Section
from core.tables import EMPTY_CELL, RowRef, TableBuilder, cell
from core.tool_types import Display, ToolContext, ToolResult
from services.chunithm import table_columns as tc
from services.chunithm.records_calc import candidate_upside
from services.common.notify import parse_channel_ref

logger = logging.getLogger(__name__)

# skills/updates는 폴더 이름에 하이픈이 있어 보통의 패키지 import 경로로 쓸 수 없다
# (goals/skill.py와 같은 이유) — 같은 폴더의 models.py/updates_calc.py를 파일 경로로
# 직접 불러온다.
_SKILL_DIR = Path(__file__).parent


def _load_sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_chunithm_updates_{name}", _SKILL_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


models = _load_sibling("models")
calc = _load_sibling("updates_calc")

_SKILL_NAME = "updates"
_SETTING_CHANNEL = "알림 채널"
_SETTING_JP_ENABLED = "일본판 알림"
_SETTING_ROLE = "역할 멘션"
_DEFAULT_JP_ENABLED = True
_NOTICE_DUE_EVENT = "updates.notice_due"
_NOTICE_FIRE_HOUR = 10
_REPORT_ORDER = 40
_ROLE_MENTION_RE = re.compile(r"^<@&\d{15,25}>$")

_MUSIC_JSON_URLS: dict[str, str] = {
    "intl": "https://chunithm.sega.com/assets/data/music.json",
    "jp": "https://chunithm.sega.jp/storage/json/music.json",
}


class Region(StrEnum):
    INTL = "intl"
    JP = "jp"


_REGION_LABELS: dict[Region, str] = {Region.INTL: "국제판", Region.JP: "일본판"}
_LISTED_SONG = "updates.listed_song"
_U_KIND = "구분"
_U_ADDED = "추가"
_U_REMOVED = "삭제"
_NOTICE_SOURCES: dict[Region, tuple[str, Any]] = {
    Region.INTL: ("https://info-chunithm.sega.com/", calc.parse_intl_news),
    Region.JP: ("https://info-chunithm.sega.jp/feed/", calc.parse_jp_rss),
}


def _now() -> datetime:
    """공지 처리용 현재 시각. 10시 알림의 fire_at은 스케줄러의 정지 시간대 계산과
    같은 서버 로컬 벽시계 기준이어야 한다(cron도 이 기준이다)."""
    return datetime.now()  # noqa: DTZ005 - cron/정지 시간대와 같은 서버 로컬 벽시계 시간 기준


def _now_utc() -> datetime:
    """곡 목록 저장본의 확인 시각. `records.last_sync_at`/`chunithm.sync.completed`의
    `previous_synced_at`가 `datetime.now(UTC)` 기준이라, 동기화 리포트 조각(order 40)이
    "그 뒤에 추가된 곡"을 맞대 비교하려면 같은 기준이어야 한다."""
    return datetime.now(UTC)


def _parse_role_ref(raw: str) -> str | None:
    """@역할 멘션(<@&id>)만 받는다. 맨 id는 안 받는다 — 역할은 멘션 문자열 그대로
    채널 글에 붙여야 실제로 핑이 가서, id만으로는 쓸모가 없다."""
    text = raw.strip()
    return text if _ROLE_MENTION_RE.match(text) else None


# ---------------------------------------------------------------- 공통 도우미


async def _broadcast_to_guilds(ctx: ToolContext, *, region: Region, content: str) -> None:
    """알림 채널을 정해 둔 서버마다, 그 서버의 스킬 켜짐·일본판 알림 설정을 보고 올린다.
    이 핸들러 자체가 길드를 훑는다 — system 플레이어가 낸 이벤트라 특정 길드로 안 좁혀진다."""
    channels = await ctx.settings.scope_ids_with_value(scope="guild", skill=_SKILL_NAME, name=_SETTING_CHANNEL)
    for guild_id, channel_id in channels:
        if not channel_id:
            continue
        if not await ctx.settings.is_skill_enabled(guild_id=guild_id, skill=_SKILL_NAME):
            continue
        if region is Region.JP:
            jp_enabled = await ctx.settings.get_value(
                scope="guild", scope_id=guild_id, skill=_SKILL_NAME, name=_SETTING_JP_ENABLED,
                default=_DEFAULT_JP_ENABLED,
            )
            if not jp_enabled:
                continue
        role_mention = await ctx.settings.get_value(
            scope="guild", scope_id=guild_id, skill=_SKILL_NAME, name=_SETTING_ROLE, default="",
        )
        full_content = f"{role_mention} {content}".strip() if role_mention else content
        await ctx.notify.post(channel_id, full_content)


async def _compose_notice_message(ctx: ToolContext, notice: Any, *, is_version_change: bool) -> str:
    region_label = _REGION_LABELS[Region(notice.region)]
    section = Section(
        order=0, data={"title": notice.title, "url": notice.url}, stage="great" if is_version_change else "good",
    )
    yuni_line = await ctx.yuni.line([section])
    header = "새 버전이 나왔어!" if is_version_change else "업데이트 공지"
    return f"[{region_label}] {header}\n{notice.title}\n{notice.url}\n{yuni_line}"


async def _dm_admins_version_change(ctx: ToolContext, notice: Any) -> None:
    region_label = _REGION_LABELS[Region(notice.region)]
    content = (
        f"[{region_label}] 버전이 '{notice.version_name}'(으)로 바뀐 것 같아. "
        f"CHUNITHM-NET 파서 점검이 필요할 수 있어.\n{notice.url}"
    )
    for player_id in await ctx.players.players_with_tag("admin"):
        await ctx.notify.dm(player_id, content)


async def _broadcast_song_diff(ctx: ToolContext, region: Region, diff: Any) -> None:
    """공지를 못 찾았는데 곡 목록만 바뀌었을 때 쓰는 대체 알림(설계서 "공지를 놓친 경우")."""
    added = ", ".join(title for _, title in diff.added) or "없음"
    removed = ", ".join(title for _, title in diff.removed) or "없음"
    section = Section(order=0, data={"added": added, "removed": removed}, stage="good")
    yuni_line = await ctx.yuni.line([section])
    region_label = _REGION_LABELS[region]
    content = f"[{region_label}] 공지 없이 곡 목록이 바뀌었어.\n추가: {added}\n삭제: {removed}\n{yuni_line}"
    await _broadcast_to_guilds(ctx, region=region, content=content)


# ---------------------------------------------------------------- 예약 작업


async def _check_notices(ctx: ToolContext, region: Region) -> bool:
    """새 공지를 저장하고(있으면) 당일 10시 알림을 건다. 이번에 하나라도 새로 찾았으면 True
    — 곡 목록 쪽이 같은 틱에서 "공지를 놓친 경우" 알림을 또 보내지 않게 하는 신호다."""
    assert ctx.db is not None
    url, parser = _NOTICE_SOURCES[region]
    response = await ctx.http.get(url)
    response.raise_for_status()
    items = parser(response.text)
    # 페이지·피드는 최신 글이 먼저 온다. 버전명 비교는 "바로 전에 안 글"과 맞대는 방식이라
    # 오래된 순으로 처리해야 한다 — 최신 순으로 그대로 돌면, 한 번에 여러 공지가 처음
    # 들어올 때(켜진 직후 등) 옛 버전이 "방금 들어온 새 버전"보다 나중에 읽혀 거꾸로
    # "버전이 바뀌었다"고 잘못 보게 된다.
    items = sorted(items, key=lambda item: item.posted_at)

    found_new = False
    for item in items:
        if await models.notice_exists(ctx.db, region=region.value, url=item.url):
            continue
        found_new = True
        last_version = await models.latest_version_name(ctx.db, region=region.value)
        is_version_change = (
            last_version is not None and item.version_name is not None and last_version != item.version_name
        )
        notice_id = await models.insert_notice(
            ctx.db, region=region.value, url=item.url, title=item.title,
            posted_at=item.posted_at.isoformat(),
            update_date=item.update_date.isoformat() if item.update_date else None,
            version_name=item.version_name, created_at=_now().isoformat(),
        )
        notice = await models.get_notice(ctx.db, notice_id)
        assert notice is not None

        if is_version_change:
            await _dm_admins_version_change(ctx, notice)

        if item.update_date is not None:
            fire_at = datetime.combine(item.update_date, time(_NOTICE_FIRE_HOUR, 0))
            assert ctx.scheduler is not None
            schedule_id = await ctx.scheduler.schedule_once(
                owner_skill=_SKILL_NAME, event_name=_NOTICE_DUE_EVENT,
                payload={"notice_id": notice_id, "version_changed": is_version_change},
                fire_at=fire_at, catch_up=True,
            )
            await models.set_schedule_id(ctx.db, notice_id, schedule_id)
        else:
            # 업데이트 날짜를 못 뽑은 공지(예: 버전 자체를 알리는 글)는 기다릴 미래 시각이
            # 없으니 바로 알린다.
            content = await _compose_notice_message(ctx, notice, is_version_change=is_version_change)
            await _broadcast_to_guilds(ctx, region=region, content=content)
            await models.mark_notified(ctx.db, notice_id)
    return found_new


async def _check_song_list(ctx: ToolContext, region: Region, *, notice_found_this_tick: bool) -> None:
    assert ctx.db is not None
    data = await ctx.http.get_json(_MUSIC_JSON_URLS[region])
    current = {str(entry["id"]): entry["title"] for entry in data}
    previous_snapshot = await models.latest_song_snapshot(ctx.db, region=region.value)
    previous = previous_snapshot.song_ids if previous_snapshot is not None else None
    diff = calc.diff_song_ids(previous, current)

    if previous is not None and not diff.added and not diff.removed:
        return
    now = _now_utc()
    await models.insert_song_snapshot(
        ctx.db, region=region.value, checked_at=now.isoformat(), song_ids=current,
        added=diff.added, removed=diff.removed,
    )
    if previous is None:
        return  # 처음 켰을 때는 기준점만 잡는다 — 수록곡 전부를 "신곡"으로 알리지 않는다.

    if region is Region.INTL and diff.added:
        await ctx.songdb.refresh_from_chunimaru(ctx.http)
    if (diff.added or diff.removed) and not notice_found_this_tick:
        await _broadcast_song_diff(ctx, region, diff)
    if diff.added or diff.removed:
        await ctx.emit(
            "chunithm.update.detected",
            {
                "region": region.value, "update_date": None,
                "added": [list(pair) for pair in diff.added], "removed": [list(pair) for pair in diff.removed],
                "version_changed": False,
            },
        )


@schedule("0 */3 * * *", catch_up=True)
async def check_updates(ctx: ToolContext) -> None:
    """두세 시간마다 일본판·국제판 공지와 곡 목록을 확인한다(서버별이 아니라 전역에서 한 번)."""
    for region in (Region.INTL, Region.JP):
        notice_found = False
        try:
            notice_found = await _check_notices(ctx, region)
        except Exception:
            logger.warning("'%s' 공지 확인 실패, 다음 주기에 다시 시도한다", region.value, exc_info=True)
        try:
            await _check_song_list(ctx, region, notice_found_this_tick=notice_found)
        except Exception:
            logger.warning("'%s' 곡 목록 확인 실패, 다음 주기에 다시 시도한다", region.value, exc_info=True)


@on(_NOTICE_DUE_EVENT)
async def on_notice_due(ctx: ToolContext, payload: dict[str, Any]) -> None:
    """예약된 당일 10시에 도착한다. 같은 공지를 두 번 알리지 않도록 notified를 본다."""
    assert ctx.db is not None
    notice_id = payload.get("notice_id")
    if notice_id is None:
        return
    notice = await models.get_notice(ctx.db, int(notice_id))
    if notice is None or notice.notified:
        return
    content = await _compose_notice_message(
        ctx, notice, is_version_change=bool(payload.get("version_changed", False))
    )
    await _broadcast_to_guilds(ctx, region=Region(notice.region), content=content)
    await models.mark_notified(ctx.db, notice.id)


# ---------------------------------------------------------------- 이벤트


@on("chunithm.sync.completed")
async def on_sync_completed(ctx: ToolContext, payload: dict[str, Any]) -> Section | None:
    """지난 동기화 뒤에 생긴 국제판 추가곡 중, 내 신곡20 프레임에 들어올 상수대인 것만
    보여준다. 상수를 아직 모르는 신곡(Chunimaru 반영 전)은 조용히 뺀다."""
    if not payload.get("has_new_plays"):
        return None
    previous_synced_at = payload.get("previous_synced_at")
    if previous_synced_at is None:
        return None
    assert ctx.db is not None

    snapshots = await models.song_snapshots_since(ctx.db, region=Region.INTL.value, since=previous_synced_at)
    added_titles = {title for snapshot in snapshots for _, title in snapshot.added}
    if not added_titles:
        return None

    floor = await ctx.records.new_frame_floor(payload["player_id"])
    fits = sorted(
        {
            hit.song.title
            for hit in ctx.songdb.find_by_const(min_const=0.0, max_const=99.0)
            if hit.song.title in added_titles
            and hit.chart.const is not None
            and candidate_upside(hit.chart.const, floor) > 0
        }
    )
    if not fits:
        return None
    return Section(
        order=_REPORT_ORDER,
        display=Display(content="새로 들어온 곡 중 네 신곡20 프레임에 들어올 만한 것: " + ", ".join(fits)),
        data={"new_songs_fit": fits},
        stage="good",
    )


# ---------------------------------------------------------------- 도구


@tool(slash="업데이트 일정")
async def next(ctx: ToolContext, region: Region) -> ToolResult:
    """알려진 다음 업데이트 날짜와 공지 링크. region은 intl(국제판)·jp(일본판)."""
    try:
        region = Region(region)
    except ValueError:
        return ToolResult(error="region은 intl, jp 중 하나여야 해.")
    assert ctx.db is not None
    today = _now().date().isoformat()
    notice = await models.next_update_notice(ctx.db, region=region.value, today=today)
    if notice is None:
        return ToolResult(
            data={"region": region.value, "update_date": None},
            display=Display(content="아직 공지가 없어."),
        )
    lines = [f"[{_REGION_LABELS[region]}] {notice.update_date} 업데이트", notice.title, notice.url]
    return ToolResult(
        data={
            "region": region.value, "update_date": notice.update_date, "title": notice.title, "url": notice.url,
        },
        display=Display(content="\n".join(lines)),
    )


@tool(slash="신곡")
async def new_songs(ctx: ToolContext, region: Region, date: str | None = None) -> ToolResult:
    """최근 업데이트의 추가·삭제 곡. region은 intl(국제판)·jp(일본판). date(ISO, YYYY-MM-DD)를
    주면 그 날짜 당일이나 그 전의 가장 최근 저장본을 본다."""
    try:
        region = Region(region)
    except ValueError:
        return ToolResult(error="region은 intl, jp 중 하나여야 해.")
    assert ctx.db is not None
    snapshot = (
        await models.song_snapshot_at_or_before(ctx.db, region=region.value, on_date=date)
        if date is not None
        else await models.latest_song_snapshot(ctx.db, region=region.value)
    )
    if snapshot is None:
        return ToolResult(
            data={"region": region.value, "added": [], "removed": []},
            display=Display(content="아직 비교할 기록이 없어."),
        )
    added = [title for _, title in snapshot.added]
    removed = [title for _, title in snapshot.removed]
    data = {"region": region.value, "checked_at": snapshot.checked_at, "added": added, "removed": removed}
    checked = snapshot.checked_at[:10]
    if not added and not removed:
        lines = [f"[{_REGION_LABELS[region]}] {checked} 확인", "추가: 없음", "삭제: 없음"]
        return ToolResult(data=data, display=Display(content="\n".join(lines)))

    builder = TableBuilder(unit="곡")
    builder.column(_U_KIND, hidden_in_text=True)  # 묶음 표지(추가/삭제)가 이미 보여 준다
    tc.add_columns(builder, tc.SONG, tc.SONG_RAW_NAME)
    builder.step("업데이트 곡 변동")
    builder.summary({"지역": _REGION_LABELS[region], "확인": checked})
    for kind, titles in ((_U_ADDED, added), (_U_REMOVED, removed)):
        if not titles:
            continue
        builder.group(kind)
        for title in titles:
            # 국제판 추가곡이 곡 DB에 있으면 곡 칸을 채워 곡 단위 행으로 쓸 수 있게 한다. 그 밖에는 곡 칸을 비우고
            # 원문만 적는다(곡 DB에 없거나 이미 빠진 곡, 일본판 곡).
            known = region is Region.INTL and kind == _U_ADDED and ctx.songdb.song_by_title(title) is not None
            builder.row(
                RowRef(_LISTED_SONG, json.dumps([region.value, title], ensure_ascii=False)),
                {
                    _U_KIND: cell(kind),
                    tc.SONG: tc.song_cell(title) if known else EMPTY_CELL,
                    tc.SONG_RAW_NAME: EMPTY_CELL if known else cell(title),
                },
            )
    return ToolResult(data=data, table=builder.build())


@tool(slash="업데이트 설정", tags=["admin"], mutates=True, confirm="always")
async def configure(
    ctx: ToolContext,
    channel: str | None = None,
    jp_updates_enabled: bool | None = None,
    role_mention: str | None = None,
) -> ToolResult:
    """업데이트 알림 채널·일본판 알림 여부·같이 멘션할 역할을 바꾼다. 안 준 값은 그대로
    둔다. channel/role_mention을 빈 문자열로 주면 끈다(채널은 #채널 멘션이나 채널 id,
    역할은 @역할 멘션으로 준다)."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 쓸 수 있어")

    result: dict[str, Any] = {}
    if channel is not None:
        channel_id = parse_channel_ref(channel) if channel else ""
        if channel and channel_id is None:
            return ToolResult(error="채널을 못 읽었어. #채널 멘션이나 채널 id로 줘.")
        await ctx.settings.set_value(
            scope="guild", scope_id=ctx.guild_id, skill=_SKILL_NAME, name=_SETTING_CHANNEL,
            value=channel_id, updated_by=ctx.player_id,
        )
        result["channel_id"] = channel_id or None

    if jp_updates_enabled is not None:
        await ctx.settings.set_value(
            scope="guild", scope_id=ctx.guild_id, skill=_SKILL_NAME, name=_SETTING_JP_ENABLED,
            value=jp_updates_enabled, updated_by=ctx.player_id,
        )
        result["jp_updates_enabled"] = jp_updates_enabled

    if role_mention is not None:
        role_ref = _parse_role_ref(role_mention) if role_mention else ""
        if role_mention and role_ref is None:
            return ToolResult(error="역할을 못 읽었어. @역할 멘션으로 줘.")
        await ctx.settings.set_value(
            scope="guild", scope_id=ctx.guild_id, skill=_SKILL_NAME, name=_SETTING_ROLE,
            value=role_ref or "", updated_by=ctx.player_id,
        )
        result["role_mention"] = role_ref or None

    if not result:
        return ToolResult(error="바꿀 값을 하나는 줘야 해.")
    return ToolResult(data=result, display=Display(ephemeral=True))
