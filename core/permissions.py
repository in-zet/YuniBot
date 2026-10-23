# core/permissions.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.manifest import DefinedTag

# "상태 태그"는 서비스가 상태를 보고 자동으로만 붙이고 뗀다. 손으로(admin의 grant_tag/
# revoke_tag 포함) 못 바꾼다. chunithm_net/records가 로그인·첫 동기화 때 실제로 붙인다.
# 설명(description/how_to_get)은 sega-account 스킬의 defines_tags가 소유한다.
STATUS_ONLY_TAGS: frozenset[str] = frozenset({"sega-linked", "chunithm-synced"})


@dataclass(slots=True, kw_only=True)
class TagRequirement:
    tags: list[str] = field(default_factory=list)
    tags_any: list[str] = field(default_factory=list)


def check_tags(requirement: TagRequirement, player_tags: set[str]) -> bool:
    """프롬프트 조립, 도구 실행기, 슬래시 경로가 공유하는 유일한 태그 검사 함수."""
    if requirement.tags and not set(requirement.tags) <= player_tags:
        return False
    return not requirement.tags_any or bool(set(requirement.tags_any) & player_tags)


class TagProvider(Protocol):
    async def tags_for(self, player_id: str) -> set[str]: ...


class SkillToggleProvider(Protocol):
    """서버(guild)에서 스킬이 켜져 있는지 묻는 자리. core/executor.py(슬래시/도구 경로)와
    core/events.py(이벤트 배선)가 같은 Protocol을 쓴다 — 둘 다 core/permissions.py에만
    의존하므로(core.manifest만 더 본다) 여기 두면 순환 import가 안 생긴다. core/executor.py가
    core/events.py를 import하기 때문에, 반대로 core/events.py가 core/executor.py에서
    이 Protocol을 가져오면 순환이 생긴다."""

    async def is_skill_enabled(self, *, guild_id: str, skill: str) -> bool: ...


class InMemoryTagProvider:
    """메모리에만 두는 태그 제공자. 운영은 players 서비스를 쓰고, 이것은 시험과 평가 스크립트용이다."""

    def __init__(self) -> None:
        self._tags: dict[str, set[str]] = {}

    async def tags_for(self, player_id: str) -> set[str]:
        return set(self._tags.get(player_id, set()))

    def grant(self, player_id: str, tag: str) -> None:
        self._tags.setdefault(player_id, set()).add(tag)

    def revoke(self, player_id: str, tag: str) -> None:
        self._tags.get(player_id, set()).discard(tag)


@dataclass(slots=True, kw_only=True)
class LockResult:
    visible: bool
    message: str | None


def describe_lock(
    *, missing_tags: list[str], tag_definitions: dict[str, DefinedTag]
) -> LockResult:
    """빠진 태그 중 하나라도 hidden이면 완전히 숨긴다(visible=False, message=None).
    전부 visible(상태 태그류)이면 얻는 방법을 안내한다."""
    if not missing_tags:
        return LockResult(visible=True, message=None)

    definitions = [tag_definitions[name] for name in missing_tags if name in tag_definitions]
    # 정의되지 않은 태그도 숨김으로 취급한다(fail-closed).
    if any(d.hidden for d in definitions) or len(definitions) != len(missing_tags):
        return LockResult(visible=False, message=None)

    hints = " ".join(d.how_to_get for d in definitions)
    return LockResult(visible=True, message=f"아직 쓸 수 없어. {hints}")
