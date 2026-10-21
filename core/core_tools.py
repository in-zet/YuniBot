from __future__ import annotations

from typing import Any

from core.decorators import tool
from core.resolvers import ResolutionError, ResolverRegistry
from core.skill_loading import SkillLoadState
from core.tables import (
    Condition,
    TableError,
    WhereClause,
    WorkTable,
    canonical_order_value,
    peek_rows,
    select_rows,
)
from core.threads import ThreadEndRequest
from core.tool_types import (
    DifficultyRef,
    LampRef,
    RankRef,
    ScoreRef,
    TableRef,
    ToolContext,
    ToolResult,
)


def _load_state(ctx: ToolContext) -> SkillLoadState | None:
    # core/agent.py가 매 턴 ctx.services["_skill_load_state"]에 이번 요청의 SkillLoadState를
    # 넣어 준다. 코어 도구 전용 내부 키라 manifest.services 선언 대상이 아니다
    # (스킬이 아니라 core/agent.py 자신이 만드는 ToolContext이기 때문).
    return ctx.services.get("_skill_load_state")  # pyright: ignore[reportReturnType]


@tool()
async def open_skill(ctx: ToolContext, skill_name: str) -> ToolResult:
    """스킬 하나를 펼친다. 이후 호출부터 그 스킬의 본문 지침과 도구 정의를 쓸 수 있다.

    여러 스킬을 한 번에 펼치려면 같은 턴에 이 도구를 스킬마다 한 번씩 부른다."""
    state = _load_state(ctx)
    if state is None:
        return ToolResult(error="스킬을 펼칠 수 없는 상태다")
    if skill_name not in state._known_skills:
        return ToolResult(error=f"'{skill_name}'이라는 스킬이 없다")
    state.open(skill_name)
    return ToolResult(data={"opened": skill_name})


@tool()
async def remember(ctx: ToolContext, content: str | None = None, nickname: str | None = None) -> ToolResult:
    """플레이어가 "이거 기억해 줘"라고 직접 요청한 내용을 플레이어 메모리에 바로 저장한다. 한 줄에 사실
    하나로 짧게 쓴다. 플레이어가 자기를 부를 이름을 정해 주면 content가 아니라 nickname에 그 이름만 넣는다."""
    if not (content and content.strip()) and not (nickname and nickname.strip()):
        return ToolResult(error="기억할 내용이나 호칭이 필요하다")
    saved = await ctx.memory.remember(ctx.player_id, content=content, nickname=nickname)
    if saved.content_saved is None and saved.nickname_saved is None:
        return ToolResult(error="저장할 수 있는 내용이 없다")
    # 확인 말은 유니가 직접 하므로 고정 문구 Display를 붙이지 않는다(답 뒤에 어색하게 덧붙는다).
    return ToolResult(data={"saved": saved.content_saved, "nickname": saved.nickname_saved})


@tool()
async def list_capabilities(ctx: ToolContext, category: str | None = None) -> ToolResult:
    """등록된 스킬을 바탕으로 할 수 있는 일을 분류별로 안내한다. category를 주면 그 분류만 보여준다."""
    # 실제 카드 목록 조립은 core/capabilities.py가 맡는다 — 여기서는 그 모듈을 부르기만 한다.
    from core.capabilities import (  # pyright: ignore[reportMissingImports]
        build_capabilities_result,
    )

    return await build_capabilities_result(ctx, category=category)


_VALUE_REF_TYPES: dict[str, type] = {
    "ScoreRef": ScoreRef,
    "RankRef": RankRef,
    "LampRef": LampRef,
    "DifficultyRef": DifficultyRef,
}


def _comparable(value_type: str, resolved: Any) -> Any:
    """해석기가 돌려준 값을 표 칸의 원래 값과 비교할 수 있는 값으로 바꾼다.
    점수는 정수, 랭크는 기준 점수(그 칸의 원래 값도 기준 점수로 둔다), 램프·난이도는 정식 이름이다."""
    if value_type == "ScoreRef":
        return int(resolved)
    if value_type == "RankRef":
        return resolved.min_score
    return resolved.canonical


async def _parse_where(ctx: ToolContext, table: WorkTable, where: list[WhereClause] | None) -> list[Condition]:
    conditions: list[Condition] = []
    resolvers = ctx.services.get("_ref_resolvers")
    for clause in where or []:
        column = table.column_by_label(clause["column"])
        if column is None:
            raise TableError(f"그 표에 '{clause['column']}' 칸이 없다")
        text = clause["value"]
        value: Any = text
        if column.order and clause["op"] != "contains":
            value = canonical_order_value(column, text)
        elif column.value_type == "number":
            try:
                value = float(text.replace(",", ""))
            except ValueError:
                raise TableError(f"'{column.label}' 칸 값 '{text}'은 숫자가 아니다") from None
        elif column.value_type in _VALUE_REF_TYPES and clause["op"] != "contains":
            if not isinstance(resolvers, ResolverRegistry) or not resolvers.is_registered(_VALUE_REF_TYPES[column.value_type]):
                raise TableError(f"'{column.label}' 칸 값을 해석할 수 없다")
            try:
                resolved = await resolvers.get(_VALUE_REF_TYPES[column.value_type]).resolve(text, ctx)
            except ResolutionError:
                raise TableError(f"'{column.label}' 칸 값 '{text}'을(를) 해석하지 못했다") from None
            value = _comparable(column.value_type, resolved)
        conditions.append(Condition(column=clause["column"], op=clause["op"], value=value, text=text))
    return conditions


def _guard_numbers(ctx: ToolContext, table: WorkTable, *numbers: list[int] | None) -> None:
    turn_ids = ctx.services.get("_turn_table_ids")
    if any(n is not None for n in numbers) and isinstance(turn_ids, list) and table.id in turn_ids:
        raise TableError("번호는 사람이 본 표에만 쓴다")


@tool()
async def table_query(
    ctx: ToolContext,
    table: TableRef,
    rows: list[int] | None = None,
    exclude_rows: list[int] | None = None,
    where: list[WhereClause] | None = None,
    sort: str | None = None,
    descending: bool = False,
    limit: int | None = None,
    group_by: str | None = None,
    columns: list[str] | None = None,
) -> ToolResult:
    """작업 표를 거르거나 정렬하거나 다시 묶어 새 표를 만든다(원래 표는 그대로). 결과 표가 사용자에게 보인다.
    rows/exclude_rows는 사람이 표에서 본 번호(1부터, 묶음을 넘어 이어짐)를 그대로 넣는다("3번부터 5번"이면 [3,4,5]).
    where는 {column: 칸 이름, op: ==,!=,<,<=,>,>=,contains, value: 사용자가 말한 값 그대로}의 목록(AND)이다.
    limit은 묶음마다 앞에서 N개다. group_by는 칸 이름이면 그 칸 값으로 다시 묶고, 빈 문자열이면 묶음을 푼다.
    묶음 순서: 숫자 칸(상수 등)과 순서가 있는 칸(버전은 출시순)으로 묶으면 큰 값(최신)부터이고, 그 칸을 sort로
    정렬하면 descending을 따른다. 글 칸(곡·작곡가 등)으로 묶으면 sort한 순서에서 그 값이 처음 나온 순서다.
    columns는 보일 칸 이름 목록이다("곡명만"이면 ["곡"]). 안 준 칸은 지우지 않고 숨기기만 한다."""
    assert isinstance(table, WorkTable)
    try:
        _guard_numbers(ctx, table, rows, exclude_rows)
        conditions = await _parse_where(ctx, table, where)
        result = select_rows(
            table, rows=rows, exclude_rows=exclude_rows, where=conditions, sort=sort, descending=descending,
            limit=limit, group_by=group_by, columns=columns,
        )
    except TableError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(table=result)


@tool()
async def table_peek(
    ctx: ToolContext,
    table: TableRef,
    rows: list[int] | None = None,
    where: list[WhereClause] | None = None,
    sort: str | None = None,
    descending: bool = False,
    limit: int = 10,
) -> ToolResult:
    """작업 표에서 고른 행의 원래 값을 읽는다(표를 만들거나 출력하지 않는다). 행 값을 말해야 할 때만 부른다.
    rows는 사람이 표에서 본 번호("3번 점수 얼마야?"면 [3]). 한 번에 최대 20행이다."""
    assert isinstance(table, WorkTable)
    try:
        _guard_numbers(ctx, table, rows)
        conditions = await _parse_where(ctx, table, where)
        data = peek_rows(table, rows=rows, where=conditions, sort=sort, descending=descending, limit=limit)
    except TableError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(data=data)


CORE_TOOL_SPECS = [
    open_skill.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
    remember.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
    list_capabilities.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
    table_query.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
    table_peek.__yuni_tool__,  # pyright: ignore[reportFunctionMemberAccess]
]


@tool()
async def end_conversation(ctx: ToolContext) -> ToolResult:
    """지금의 대화 스레드를 끝낸다. 플레이어가 대화를 끝내려는 뜻을 분명히 밝혔을 때만 부른다(예: "이만할게",
    "끝났어", "다음에 얘기하자"). 애매하면 부르지 말고 끝낼지 되묻는다. 먼저 짧게 마무리하는 말을 쓰고, 그 말을
    보낸 뒤 대화가 닫힌다."""
    request = ctx.services.get("_thread_end")
    if not isinstance(request, ThreadEndRequest):
        return ToolResult(error="끝낼 대화 스레드가 아니다")
    request.requested = True
    return ToolResult(data={"ending": True})


# 대화 스레드 안에서만 모델에게 보이는 코어 도구.
THREAD_TOOL_SPECS = [end_conversation.__yuni_tool__]  # pyright: ignore[reportFunctionMemberAccess]
