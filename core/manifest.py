from __future__ import annotations

import re
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
        "폴더",
        "유니 설정",
    }
)
VALID_GAMES: frozenset[str] = frozenset({"chunithm", "common"})

# 00-core.md: name은 "소문자, 숫자, 하이픈"이고 64자 이내다.
MAX_NAME_LENGTH = 64
_NAME_RE = re.compile(rf"[a-z0-9-]{{1,{MAX_NAME_LENGTH}}}")


class DefinedTag(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    name: str
    description: str
    how_to_get: str = ""
    hidden: bool = False


class SettingSpec(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    name: str
    kind: Literal["bool", "int", "float", "str"]
    default: Any
    description: str
    scope: Literal["player", "guild"] = "player"


class SkillManifest(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    name: str
    description: str
    # `/도움말`이 플레이어에게 보여 주는 한두 문장(유니 말투). 비우면 description을 쓴다.
    summary: str = ""
    examples: list[str]
    # 메시지에 이 낱말이 들어 있으면 코어가 이 스킬을 처음부터 펼쳐 준다(00-core.md "스킬 불러오기").
    # 모델이 open_skill을 부르는 왕복 한 번을 아끼는 용도라, 틀려도 손해는 펼친 지침 분량뿐이다.
    triggers: list[str] = []
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
    # heavy는 CHUNITHM-NET을 실시간으로 스크래핑하는 등 오래 걸릴 걸로 예상되는 스킬이다
    # (core/mention.py가 이 스킬의 도구를 처음 부를 때 "확인하고 있다" 안내를 바로 보낸다).
    # light(기본)인 스킬만 쓰인 턴은 아무리 오래 걸려도 그 안내를 보내지 않는다.
    weight: Literal["light", "heavy"] = "light"


def parse_manifest(text: str) -> SkillManifest:
    """SKILL.md 전체 텍스트에서 '---'로 감싼 YAML 프런트매터를 읽어 SkillManifest로 검증한다."""
    frontmatter = _extract_frontmatter(text)
    try:
        raw: object = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise ManifestError(f"프런트매터 YAML 문법 오류: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("프런트매터가 YAML 매핑(dict)이 아니다")

    try:
        manifest = msgspec.convert(raw, type=SkillManifest, strict=False)
    except msgspec.ValidationError as exc:
        raise ManifestError(f"manifest 검증 실패: {exc}") from exc

    if not _NAME_RE.fullmatch(manifest.name):
        # 이름은 도구 이름의 접두사가 되고, 슬래시 콜백 이름을 만드는 코드 생성(core/slash.py)까지
        # 흘러간다. 여기서 막아야 이상한 이름 하나가 봇 전체 기동을 깨뜨리지 않는다.
        raise ManifestError(
            f"name '{manifest.name}'은 소문자, 숫자, 하이픈만 쓸 수 있다(1~{MAX_NAME_LENGTH}자)"
        )
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
