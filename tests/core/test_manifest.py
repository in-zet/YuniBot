import pytest

from core.manifest import ManifestError, parse_manifest

BORDER_EXAMPLE = """---
name: chunithm-border
description: 곡과 난이도별 랭크 보더(허용 어택 개수)와 목표 점수까지 남은 어택을 계산한다.
examples:
  - 크로노 마스 SSS+ 보더 알려줘
  - 내 최고점에서 트플플까지 몇 어택 남았어?
category: 곡 정보
game: chunithm
version: 1
services: [songdb, records]
---

본문은 무시된다.
"""


def test_parse_manifest_happy_path() -> None:
    manifest = parse_manifest(BORDER_EXAMPLE)

    assert manifest.name == "chunithm-border"
    assert manifest.category == "곡 정보"
    assert manifest.game == "chunithm"
    assert manifest.version == 1
    assert manifest.services == ["songdb", "records"]
    assert manifest.tags == []
    assert manifest.enabled is True


def test_parse_manifest_missing_frontmatter() -> None:
    with pytest.raises(ManifestError, match="프런트매터"):
        parse_manifest("그냥 본문만 있음")


def test_parse_manifest_missing_required_field() -> None:
    text = "---\nname: x\n---\n본문"
    with pytest.raises(ManifestError, match="description"):
        parse_manifest(text)


def test_parse_manifest_malformed_yaml_raises_manifest_error() -> None:
    text = BORDER_EXAMPLE.replace("services: [songdb, records]", "services: [songdb, records")
    with pytest.raises(ManifestError):
        parse_manifest(text)


def test_parse_manifest_unknown_top_level_key_raises() -> None:
    text = BORDER_EXAMPLE.replace("services: [songdb, records]", "servicess: [songdb, records]")
    with pytest.raises(ManifestError):
        parse_manifest(text)


def test_parse_manifest_bad_category() -> None:
    text = BORDER_EXAMPLE.replace("category: 곡 정보", "category: 없는분류")
    with pytest.raises(ManifestError, match="category"):
        parse_manifest(text)


def test_parse_manifest_bad_game() -> None:
    text = BORDER_EXAMPLE.replace("game: chunithm", "game: maimai")
    with pytest.raises(ManifestError, match="game"):
        parse_manifest(text)


def test_parse_manifest_defines_tags_and_settings() -> None:
    text = (
        "---\n"
        "name: demo\n"
        "description: 데모\n"
        "examples: [하나]\n"
        "category: 메모\n"
        "game: common\n"
        "version: 1\n"
        "services: []\n"
        "defines_tags:\n"
        "  - name: demo-member\n"
        "    description: 데모 체험 완료\n"
        "    how_to_get: /연습 체험시작 실행\n"
        "    hidden: false\n"
        "settings:\n"
        "  - name: verbose\n"
        "    kind: bool\n"
        "    default: false\n"
        "    description: 자세히 보기\n"
        "---\n본문"
    )

    manifest = parse_manifest(text)

    assert manifest.defines_tags[0].name == "demo-member"
    assert manifest.defines_tags[0].hidden is False
    assert manifest.settings[0].kind == "bool"
    assert manifest.settings[0].scope == "player"


def test_parse_manifest_setting_scope_guild() -> None:
    text = (
        "---\n"
        "name: demo2\n"
        "description: 데모\n"
        "examples: [하나]\n"
        "category: 메모\n"
        "game: common\n"
        "version: 1\n"
        "services: []\n"
        "settings:\n"
        "  - name: 오랜만의 기준\n"
        "    kind: int\n"
        "    default: 14\n"
        "    description: 며칠이면 오랜만인지\n"
        "    scope: guild\n"
        "---\n본문"
    )
    manifest = parse_manifest(text)
    assert manifest.settings[0].scope == "guild"


@pytest.mark.parametrize(
    "bad_name",
    ["Chunithm-Border", "my_skill", "스킬", "foo bar", "x'); import os; ('", "", "a" * 65],
)
def test_parse_manifest_rejects_invalid_name(bad_name: str) -> None:
    # name은 "소문자, 숫자, 하이픈"만 쓴다(00-core.md). 도구 이름의 접두사가 되고, 슬래시 콜백
    # 이름을 만드는 코드 생성 단계까지 흘러가므로 매니페스트 단계에서 막아야 한다.
    text = BORDER_EXAMPLE.replace("name: chunithm-border", f'name: "{bad_name}"')
    with pytest.raises(ManifestError, match="name"):
        parse_manifest(text)


def test_parse_manifest_accepts_lowercase_digits_hyphens() -> None:
    text = BORDER_EXAMPLE.replace("name: chunithm-border", "name: yuni-bond-2")
    assert parse_manifest(text).name == "yuni-bond-2"
