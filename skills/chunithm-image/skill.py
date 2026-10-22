# skills/chunithm-image/skill.py
from __future__ import annotations

import importlib.util
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from core.decorators import tool
from core.tables import TableError, TableNeeds, WorkTable
from core.tool_types import Display, DisplayFile, TableRef, ToolContext, ToolResult
from services.chunithm import table_columns as tc
from services.chunithm.image_cache import jacket_url

# skills/chunithm-image는 폴더 이름에 하이픈이 있어 보통의 패키지 import 경로로 쓸 수 없다 — 같은 폴더의
# render.py를 파일 경로로 직접 불러온다(chunithm-goals와 같은 방식).
_SKILL_DIR = Path(__file__).parent


def _load_sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"_chunithm_image_{name}", _SKILL_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass가 모듈 전역을 되찾아야 한다
    spec.loader.exec_module(module)
    return module


render = _load_sibling("render")


class Layout(StrEnum):
    CARD = "card"
    LIST = "list"


_NICKNAME_KEY = "닉네임"


async def _header_profile(ctx: ToolContext, table: WorkTable) -> tuple[Any, list[str | None]]:
    """머리말에 그릴 플레이어 정보와, 받아야 할 그림 주소(캐릭터·프레임). 표에 `subject`가 없으면 None."""
    if table.subject is None:
        return None, []
    profile = await ctx.records.player_profile(table.subject)
    nickname: str | None = None
    if _NICKNAME_KEY not in table.summary:
        summary = await ctx.records.latest_sync_summary(table.subject)
        nickname = summary.nickname if summary is not None else None
    if profile is None and nickname is None:
        return None, []
    urls = [profile.character_image_url, profile.character_frame_url] if profile is not None else []
    return (nickname, profile), urls


@tool()
async def table_image(
    ctx: ToolContext,
    table: Annotated[TableRef, TableNeeds(nonempty=[tc.SONG])],
    layout: Layout = Layout.CARD,
) -> ToolResult:
    """표를 재킷이 들어간 그림으로 그려 보낸다. 그림을 달라고 했을 때만 쓴다. layout은 안 주면 card(재킷 카드 격자)다. 사용자가 "목록", "리스트", "한 줄씩"이라고 말했을 때만 list를 준다 — 정렬 순서나 곡 수가 많은 것만으로 list를 고르지 않는다."""
    assert isinstance(table, WorkTable)
    try:
        render.check_extras(table, Layout(layout).value)
    except TableError as exc:
        return ToolResult(error=str(exc))
    titles = list(dict.fromkeys(row.cell(tc.SONG).text for row in table.rows))
    jacket_urls = {title: jacket_url(ctx.songdb.jacket_for(title)) for title in titles}
    extra, profile_urls = await _header_profile(ctx, table)

    fetched = await ctx.image_cache.fetch_many([*jacket_urls.values(), *profile_urls])
    jackets = {title: fetched.get(url) if url else None for title, url in jacket_urls.items()}

    # 가동 버전은 레이팅표(요약에 최종 레이팅이 있는 표)에만 머리말에 넣는다.
    version = ctx.songdb.current_version() if render.header_rating(table) is not None else None
    header = None
    if extra is not None:
        nickname, profile = extra
        header = render.HeaderProfile(
            nickname=nickname,
            level=profile.level if profile is not None else None,
            character=fetched.get(profile.character_image_url) if profile is not None and profile.character_image_url else None,
            frame=fetched.get(profile.character_frame_url) if profile is not None and profile.character_frame_url else None,
            version=version,
        )
    elif version is not None:
        header = render.HeaderProfile(version=version)
    stale = bool(
        ctx.tables is not None and ctx.channel_id is not None
        and ctx.tables.is_stale(ctx.player_id, ctx.channel_id, table.id)
    )

    files = render.render_pages(table, layout=Layout(layout).value, jackets=jackets, profile=header, stale=stale)
    missing = sum(1 for data in jackets.values() if data is None)
    return ToolResult(
        data={
            "table": table.id, "images": len(files), "rows": table.row_count, "missing_jackets": missing,
            "drawn_columns": render.drawn_labels(table),
        },
        display=Display(files=[DisplayFile(name, data) for name, data in files]),
        drew_table=table.id,
    )
