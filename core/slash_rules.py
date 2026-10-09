"""슬래시 경로와 도구 인자 이름 규칙.

레지스트리(core/registry.py)가 스킬을 로드할 때 이 함수들로 검사해 어긴 스킬을 이유와 함께
개별 비활성화한다(방어선 1). core/slash.py의 attach_to_tree와 콜백 생성도 같은 함수로 한 번
더 확인한다(방어선 2). 레지스트리와 slash.py가 서로를 import하지 않도록 따로 둔다.
"""

from __future__ import annotations

import keyword
from collections.abc import Iterable

from discord.app_commands.commands import validate_name as _discord_validate_name

MAX_SLASH_DEPTH = 3  # 디스코드: 명령, 그룹, 하위 명령의 세 단계까지


def _is_valid_discord_name(name: str) -> bool:
    try:
        _ = _discord_validate_name(name)
    except ValueError:
        return False
    return True


def slash_path_problems(slash: str) -> list[str]:
    """slash 경로 문자열 하나의 문제를 모두 돌려준다. 문제가 없으면 빈 리스트."""
    tokens = slash.split()
    if not tokens:
        return [f"슬래시 경로가 비어 있다: {slash!r}"]
    problems: list[str] = []
    if len(tokens) > MAX_SLASH_DEPTH:
        problems.append(f"슬래시 경로는 최대 {MAX_SLASH_DEPTH}단계다: '{slash}'")
    for token in tokens:
        if not _is_valid_discord_name(token):
            problems.append(
                f"슬래시 이름 '{token}'은 디스코드 명령 이름 규칙(소문자, 1~32자, 글자/숫자/-/_)에 "
                + f"맞지 않는다: '{slash}'"
            )
    return problems


def callback_arg_name_problems(arg_names: Iterable[str]) -> list[str]:
    """도구 인자 이름이 생성한 슬래시 콜백(core/slash.py의 _build_callback_source)과 디스코드
    옵션 이름으로 쓸 수 있는지 검사한다. 문제가 없으면 빈 리스트."""
    problems: list[str] = []
    for name in arg_names:
        if not name.isidentifier() or keyword.iskeyword(name) or name.startswith("__"):
            problems.append(
                f"도구 인자 이름 '{name}'은 슬래시 콜백에 쓸 수 없다"
                + "(식별자가 아니거나, 예약어이거나, '__'로 시작해 내부 이름과 겹친다)"
            )
        elif not _is_valid_discord_name(name):
            problems.append(
                f"도구 인자 이름 '{name}'은 디스코드 옵션 이름 규칙(소문자, 1~32자)에 맞지 않는다"
            )
    return problems
