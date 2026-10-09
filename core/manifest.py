from __future__ import annotations

from typing import Any, Literal

import msgspec
import yaml


class ManifestError(ValueError):
    pass


VALID_CATEGORIES: frozenset[str] = frozenset(
    {
        "계정 연결",
        "곡 정보",
        "기록과 리포트",
        "목표",
        "세션과 후기",
        "오락실",
        "메모",
        "알림",
        "질문 답변",
        "메모리",
        "함께한 기록",
        "관리",
    }
)
VALID_GAMES: frozenset[str] = frozenset({"chunithm", "common"})


class DefinedTag(msgspec.Struct, kw_only=True):
    name: str
    description: str
    how_to_get: str = ""
    hidden: bool = False


class SettingSpec(msgspec.Struct, kw_only=True):
    name: str
    kind: Literal["bool", "int", "float", "str"]
    default: Any
    description: str


class SkillManifest(msgspec.Struct, kw_only=True):
    name: str
    description: str
    examples: list[str]
    category: str
    game: str
    version: int
    services: list[str] = []
    tags: list[str] = []
    tags_any: list[str] = []
    defines_tags: list[DefinedTag] = []
    emits: list[str] = []
    listens: list[str] = []
    settings: list[SettingSpec] = []
    enabled: bool = True


def parse_manifest(text: str) -> SkillManifest:
    """SKILL.md 전체 텍스트에서 '---'로 감싼 YAML 프런트매터를 읽어 SkillManifest로 검증한다."""
    frontmatter = _extract_frontmatter(text)
    raw: object = yaml.safe_load(frontmatter)
    if not isinstance(raw, dict):
        raise ManifestError("프런트매터가 YAML 매핑(dict)이 아니다")

    try:
        manifest = msgspec.convert(raw, type=SkillManifest, strict=False)
    except msgspec.ValidationError as exc:
        raise ManifestError(f"manifest 검증 실패: {exc}") from exc

    if manifest.category not in VALID_CATEGORIES:
        raise ManifestError(
            f"category '{manifest.category}'는 허용된 분류가 아니다: {sorted(VALID_CATEGORIES)}"
        )
    if manifest.game not in VALID_GAMES:
        raise ManifestError(f"game '{manifest.game}'은 'chunithm' 또는 'common'이어야 한다")

    return manifest


def _extract_frontmatter(text: str) -> str:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        raise ManifestError("SKILL.md에 '---' YAML 프런트매터가 없다")
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        raise ManifestError("SKILL.md 프런트매터가 닫히지 않았다('---'가 2개 필요)")
    return parts[1]
