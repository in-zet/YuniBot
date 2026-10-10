from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


class _ManifestLike(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def game(self) -> str: ...
    @property
    def version(self) -> int: ...


class _LoadedSkillLike(Protocol):
    @property
    def manifest(self) -> _ManifestLike: ...


class _SkillRegistryLike(Protocol):
    """core.registry.SkillRegistry가 구조적으로 맞는 자리.

    core.registry는 core.schema를 거쳐 core.tool_types로 이어지고, 이 모듈은
    core.tool_types(ToolContext.registry_snapshot 필드)에서 쓰인다. 그래서
    core.registry.SkillRegistry를 (타입 검사 전용이라도) 직접 import하면
    registry -> schema -> tool_types -> registry_snapshot -> registry로
    돌아오는 순환 import가 된다. 여기서는 실제로 쓰는 모양(enabled_skills,
    failures)만 구조적 타입으로 적어 그 edge 자체를 없앤다."""

    def enabled_skills(self) -> Iterable[_LoadedSkillLike]: ...
    def failures(self) -> dict[str, str]: ...


@dataclass(slots=True, kw_only=True, frozen=True)
class SkillSummary:
    name: str
    game: str
    version: int
    enabled: bool


@dataclass(slots=True, kw_only=True, frozen=True)
class RegistrySnapshot:
    skills: tuple[SkillSummary, ...]
    failures: dict[str, str]


def build_snapshot(registry: _SkillRegistryLike) -> RegistrySnapshot:
    """레지스트리의 읽기 전용 스냅샷을 한 번 만든다. 스킬은 레지스트리를 직접 못 보므로
    admin의 `skills` 도구가 이 스냅샷으로만 등록 현황을 본다."""
    skills = tuple(
        SkillSummary(name=s.manifest.name, game=s.manifest.game, version=s.manifest.version, enabled=True)
        for s in registry.enabled_skills()
    )
    return RegistrySnapshot(skills=skills, failures=registry.failures())
