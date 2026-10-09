# core/permissions.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from core.manifest import DefinedTag


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


class InMemoryTagProvider:
    """players 서비스(3단계)가 생기기 전까지 쓰는 임시 태그 제공자."""

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
    # 정의되지 않은 태그도 숨김으로 취급한다(fail-closed). 3단계 레지스트리 전까지의 기본값.
    if any(d.hidden for d in definitions) or len(definitions) != len(missing_tags):
        return LockResult(visible=False, message=None)

    hints = "; ".join(f"{d.description} ({d.how_to_get})" for d in definitions)
    return LockResult(visible=True, message=f"아직 쓸 수 없어요. {hints}")
