# 2단계: 코어 뼈대 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 스킬 레지스트리, 5개 장식자(`@tool` `@on` `@schedule` `@prompt_context` `@slash_only`), 표준 인자 타입과 해석기 자리, 슬래시 경로 생성, 도구 실행기, 공유 권한 태그 검사 함수, 이벤트 버스, 대화 스레드, SQL 마이그레이션 실행기, 계약 테스트, `skills/_template/`을 만든다. 끝나면 코어를 고치지 않고 스킬 하나를 추가해 슬래시 명령으로 쓸 수 있고, 태그 조건이 걸린 스킬은 태그 없는 플레이어에게 막히며 안내가 나오고, 규격에 어긋난 스킬은 이유를 남기고 등록되지 않는다.

**Architecture:** `core/`는 전부 새 파일이다(1단계는 `third_party/`만 가져왔다). 의존 방향은 `manifest → decorators/schema/resolvers/permissions → registry → executor/slash → bot`이고, `events`/`threads`/`migrations`는 독립 모듈로 다른 모듈에 거의 의존하지 않는다. `ToolContext`는 `__getattr__`로 manifest에 선언된 서비스만 노출해, "선언하지 않은 서비스 사용"을 런타임에서도 한 번 더 막는다(정적 검사는 레지스트리가 AST로 한다). `players` 서비스가 아직 없으므로 권한 태그는 `InMemoryTagProvider`(코어가 제공하는 최소 구현, 3단계에서 `players`로 교체될 때까지 쓰는 자리)로 시험한다. 연습용 스킬과 깨진 스킬 픽스처는 `tests/fixtures/`에 두어, 배포 대상인 `skills/`(지금은 `_template/`만 있음)와 분리한다 — 레지스트리는 스킬 디렉터리 "목록"을 설정으로 받으므로, 연습용 스킬을 켜는 것도 코어를 고치는 일이 아니라 `SKILLS_DIRS` 환경변수에 디렉터리 하나를 더 적는 설정 변경이다.

**합의된 단순화 (사용자 확인, 설계서 미기재 사항):** "부여 태그는 숨겨진다"는 규칙은 Discord 네이티브 멤버별 명령 권한 동기화로 구현하지 않는다. 슬래시 명령 자체는 전역/길드 단위로 등록되어 모두에게 보이되(디스코드 자체 한계), `hidden=true`인 태그가 없는 플레이어가 실행하면 일반적인 "모르는 명령"류의 응답을 받는다(안내문 없음). `hidden=false`(상태 태그)인 경우에만 "~하면 쓸 수 있어요" 안내문을 준다. 이 차이가 실제 동작 차이이고, Discord 명령 피커에서의 완전한 비가시성은 6단계 이후 `list_capabilities`/카드 필터링의 몫이다.

**Tech Stack:** Python 3.14, discord.py 2.7(app_commands), msgspec(스키마/검증), PyYAML(SKILL.md 프런트매터), python-dotenv(로컬 실행 편의), aiosqlite, pytest, ruff, basedpyright.

**Spec:** `docs/design/00-core.md` ("유니 코어", "스킬 모듈 표준", "스킬 불러오기", "이벤트, 예약 작업, 서비스", "데이터, 권한, 비밀 값", "저장소 구조와 개발 규칙", "작업 순서"의 2단계), `CLAUDE.md`. 스킬 문서(`docs/design/skills/*.md`)는 매니페스트 필드 실사용 예(`admin.md`, `account-link.md`)로만 참고했다.

## Global Constraints

- 2단계는 LLM, 에이전트 반복, 프롬프트 조립 본체, `[[show:id]]` 숫자 대조를 만들지 않는다(6단계). `@schedule`은 장식자와 등록 정보까지만 만들고 저장·실행은 3단계다. 공통 서비스(`players` 등)와 실제 스킬은 만들지 않고, 필요한 자리는 인터페이스+가짜 구현으로 둔다.
- 코어는 츄니즘을 모른다. 이번 단계 코드 어디에도 츄니즘 고유 지식을 넣지 않는다.
- 스킬은 `manifest.services`에 적은 서비스만 `ctx`로 쓴다. 다른 스킬의 테이블/설정을 읽지 않는다.
- 새 스킬은 `skills/_template/`을 복사해 만든다. 스킬을 더하려고 코어나 다른 스킬을 고쳐야 한다면 규격이 모자라다는 뜻이다 — 이번 단계의 실제 검증 대상이다(통과 기준 1).
- SQLite + aiosqlite. ORM/alembic 없음. 쿼리는 SQL 문 직접 작성.
- 마이그레이션은 `migrations/NNN_설명.sql`. 코어가 먼저, 스킬은 이름순. 한 번 적용한 파일은 고치지 않는다 — 이번 단계는 내용이 바뀐 기적용 파일을 감지하면 실패시킨다(체크섬).
- 스킬 테이블 접두사는 스킬 이름에서 `chunithm-`을 빼고 하이픈을 밑줄로 바꾼 것 + `_`.
- 권한 태그 검사는 프롬프트 조립, 도구 실행기, 슬래시 경로가 같은 함수(`core.permissions.check_tags`)를 쓴다. 이번 단계는 프롬프트 조립이 없으므로 도구 실행기와 슬래시 경로 두 곳에서 실제로 같은 함수를 호출하는 것으로 확인한다.
- `ruff`/`basedpyright`는 `third_party/`를 보지 않는다(이미 설정됨). 새 코드는 `core/`, `tests/`에만 추가한다.
- `.env`, `data/`는 커밋하지 않는다(이미 `.gitignore`에 있음).

## Review Focus

- **깨진 스킬 하나가 레지스트리 전체를 죽이는 경우**: 매니페스트 파싱 실패, 미선언 서비스 사용, 정의 안 된 태그 사용 등은 그 스킬만 비활성화하고 이유를 남겨야 하며, 다른 정상 스킬의 등록을 막으면 안 된다(Task 10에서 "정상 스킬 1개 + 깨진 스킬 여러 개" 픽스처로 검증).
- **슬래시 이름 충돌이 조용히 한쪽을 덮어쓰는 경우**: 같은 경로를 두 스킬이 쓰거나, 한 스킬이 단독 명령으로 쓴 이름을 다른 스킬이 그룹(하위 명령 보유)으로 쓰면 반드시 명시적 실패/비활성화로 드러나야 한다(Task 10, Task 11).
- **모으기 이벤트에서 느린 핸들러가 전체를 붙잡는 경우**: 핸들러 하나가 시간 제한을 넘기면 그 조각만 빠지고 나머지 핸들러와 결과 조립은 계속돼야 한다(Task 7).
- **대화 스레드가 멈춰서 안 닫히는 경우**: 무응답 확인 이후에도 응답이 없으면 반드시 자동으로 닫히고 전문이 콜백으로 전달돼야 하며, 메시지가 오면 타이머가 리셋돼야 한다(Task 8).
- **마이그레이션이 절반만 적용된 채 "성공"으로 기록되는 경우**: 파일 하나의 SQL이 도중에 실패하면 그 파일의 트랜잭션은 전부 롤백되어야 하고 `schema_migrations`에 기록되면 안 되며, 호출자는 예외를 받아야 한다(Task 9).

---

## Task 1: 의존성 추가와 설정 로더

**Files:**
- Modify: `pyproject.toml` (의존성 추가)
- Create: `core/__init__.py`
- Create: `core/config.py`
- Create: `tests/core/__init__.py`
- Create: `tests/core/test_config.py`

**Interfaces:**
- Consumes: 없음
- Produces: `CoreConfig` (`discord_token: str`, `skills_dirs: list[pathlib.Path]`, `db_path: pathlib.Path`, `test_guild_id: int | None`), `load_config() -> CoreConfig` — 이후 모든 Task가 이 설정 구조를 바탕으로 경로를 받는다.

- [ ] **Step 1: 의존성 추가**

```bash
cd /home/dn/projects/YuniBot
uv add pyyaml python-dotenv
uv add --dev pytest-asyncio
```

`pyproject.toml`에 `[tool.pytest.ini_options]`의 `pythonpath = ["third_party"]` 아래에 비동기 테스트 모드를 추가한다:

```toml
[tool.pytest.ini_options]
pythonpath = ["third_party"]
asyncio_mode = "auto"
```

- [ ] **Step 2: 실패하는 설정 테스트 작성**

```python
# tests/core/test_config.py
import os
from pathlib import Path

import pytest

from core.config import load_config


def test_load_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("SKILLS_DIRS", "skills,tests/fixtures/skills_ok")
    monkeypatch.setenv("DB_PATH", "data/test.db")
    monkeypatch.setenv("TEST_GUILD_ID", "123456789")

    config = load_config()

    assert config.discord_token == "fake-token"
    assert config.skills_dirs == [Path("skills"), Path("tests/fixtures/skills_ok")]
    assert config.db_path == Path("data/test.db")
    assert config.test_guild_id == 123456789


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.delenv("SKILLS_DIRS", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("TEST_GUILD_ID", raising=False)

    config = load_config()

    assert config.skills_dirs == [Path("skills")]
    assert config.db_path == Path("data/yuni.db")
    assert config.test_guild_id is None


def test_load_config_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="DISCORD_TOKEN"):
        load_config()
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_config.py -v
```

Expected: FAIL (`core.config` 모듈 없음)

- [ ] **Step 4: 최소 구현**

```python
# core/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True, kw_only=True)
class CoreConfig:
    discord_token: str
    skills_dirs: list[Path]
    db_path: Path
    test_guild_id: int | None


def load_config() -> CoreConfig:
    """환경변수에서 코어 설정을 읽는다. 이미 설정된 환경변수는 .env로 덮어쓰지 않는다."""
    load_dotenv(override=False)

    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN 환경변수가 없다")

    skills_dirs_raw = os.environ.get("SKILLS_DIRS", "skills")
    skills_dirs = [Path(part.strip()) for part in skills_dirs_raw.split(",") if part.strip()]

    db_path = Path(os.environ.get("DB_PATH", "data/yuni.db"))

    guild_raw = os.environ.get("TEST_GUILD_ID")
    test_guild_id = int(guild_raw) if guild_raw else None

    return CoreConfig(
        discord_token=token,
        skills_dirs=skills_dirs,
        db_path=db_path,
        test_guild_id=test_guild_id,
    )
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_config.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 6: 커밋**

```bash
git add pyproject.toml uv.lock core/__init__.py core/config.py tests/core/__init__.py tests/core/test_config.py
git commit -m "core: add config loader and stage-2 test dependencies"
```

---

## Task 2: 표준 타입 — ToolContext, ToolResult, Display, 표준 인자 타입 마커

**Files:**
- Create: `core/tool_types.py`
- Create: `tests/core/test_tool_types.py`

**Interfaces:**
- Consumes: 없음
- Produces: `ToolContext(player_id: str, tags: frozenset[str], services: Mapping[str, object])`, `ToolResult(data: dict, display: Display | None, display_mode: Literal["required","optional"], error: str | None)`, `Display(content: str | None, embeds: list, view: object | None)`, 표준 인자 마커 클래스 `PlayerRef`, `SongQuery`, `ChartRef`, `ArcadeRef`, `RankRef`, `DifficultyRef`, `LampRef`, `ScoreRef`, `STANDARD_REF_TYPES: frozenset[type]`, `UndeclaredServiceError`. Task 5(schema)·Task 6(resolvers)·Task 11(registry)·Task 12(executor)가 이 타입들을 가져다 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_tool_types.py
import pytest

from core.tool_types import (
    ChartRef,
    Display,
    STANDARD_REF_TYPES,
    ToolContext,
    ToolResult,
    UndeclaredServiceError,
)


class _FakeSongdb:
    def ping(self) -> str:
        return "pong"


def test_tool_context_exposes_declared_service() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={"songdb": _FakeSongdb()})
    assert ctx.songdb.ping() == "pong"


def test_tool_context_rejects_undeclared_service() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={"songdb": _FakeSongdb()})
    with pytest.raises(UndeclaredServiceError, match="records"):
        _ = ctx.records


def test_tool_result_defaults() -> None:
    result = ToolResult(data={"borders": 1})
    assert result.display is None
    assert result.display_mode == "required"
    assert result.error is None


def test_display_mode_optional() -> None:
    result = ToolResult(data={}, display=Display(content="hi"), display_mode="optional")
    assert result.display_mode == "optional"


def test_standard_ref_types_registered() -> None:
    assert ChartRef in STANDARD_REF_TYPES
    assert len(STANDARD_REF_TYPES) == 8
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_tool_types.py -v
```

Expected: FAIL (`core.tool_types` 없음)

- [ ] **Step 3: 구현**

```python
# core/tool_types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


class UndeclaredServiceError(AttributeError):
    def __init__(self, service_name: str) -> None:
        super().__init__(
            f"이 스킬은 서비스 '{service_name}'를 manifest의 services에 선언하지 않았다"
        )
        self.service_name = service_name


@dataclass(slots=True, kw_only=True)
class ToolContext:
    """도구/이벤트 핸들러/예약 작업에 전달되는 실행 맥락. manifest.services에 없는 서비스는 접근 자체가 실패한다."""

    player_id: str
    tags: frozenset[str]
    services: Mapping[str, object] = field(default_factory=dict)

    def __getattr__(self, name: str) -> object:
        # dataclass가 정의한 필드(player_id, tags, services)는 정상 __getattribute__로 먼저 해석되므로
        # 여기 도달하는 것은 선언되지 않은 속성/서비스 접근뿐이다.
        services = object.__getattribute__(self, "services")
        if name in services:
            return services[name]
        raise UndeclaredServiceError(name)


@dataclass(slots=True, kw_only=True)
class Display:
    """슬래시/멘션 응답에 붙일 표시물. 코어는 내용을 고치지 않고 그대로 보낸다."""

    content: str | None = None
    embeds: list[Any] = field(default_factory=list)
    view: Any | None = None


@dataclass(slots=True, kw_only=True)
class ToolResult:
    data: dict[str, Any] = field(default_factory=dict)
    display: Display | None = None
    display_mode: Literal["required", "optional"] = "required"
    error: str | None = None


class PlayerRef:
    """모델/슬래시가 넘긴 원문(이름, 호칭, 멘션, "나")을 실제 플레이어로 바꾸는 자리. 해석은 3단계 players 서비스."""


class SongQuery:
    """곡 이름/별칭/줄임말을 곡 하나로 좁히는 자리. 해석은 4단계 songdb 서비스."""


class ChartRef:
    """SongQuery + 난이도를 채보(상수, 노트 수 포함)로 바꾸는 자리."""


class ArcadeRef:
    """오락실 이름/별칭/줄임말을 오락실로 바꾸는 자리. 해석은 공통 arcades 서비스."""


class RankRef:
    """랭크 표현(정식/은어)을 정식 랭크와 기준 점수로 바꾸는 자리. 해석은 terms 서비스."""


class DifficultyRef:
    """난이도 표현을 정식 난이도로 바꾸는 자리."""


class LampRef:
    """램프 표현을 램프(와 있으면 기준 점수)로 바꾸는 자리."""


class ScoreRef:
    """점수 표현(숫자/랭크/램프 이름)을 0~1,010,000 점수로 바꾸는 자리."""


STANDARD_REF_TYPES: frozenset[type] = frozenset(
    {PlayerRef, SongQuery, ChartRef, ArcadeRef, RankRef, DifficultyRef, LampRef, ScoreRef}
)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_tool_types.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/tool_types.py tests/core/test_tool_types.py
git commit -m "core: add ToolContext, ToolResult, Display, standard ref type markers"
```

---

## Task 3: 매니페스트 파싱과 검증

**Files:**
- Create: `core/manifest.py`
- Create: `tests/core/test_manifest.py`

**Interfaces:**
- Consumes: 없음
- Produces: `SkillManifest`(msgspec.Struct: `name, description, examples, category, game, version, services, tags, tags_any, defines_tags, emits, listens, settings, enabled`), `DefinedTag(name, description, how_to_get, hidden)`, `SettingSpec(name, kind, default, description)`, `ManifestError`, `parse_manifest(text: str) -> SkillManifest`, `VALID_CATEGORIES: frozenset[str]`, `VALID_GAMES: frozenset[str]`. Task 11(registry)이 디스크에서 읽은 `SKILL.md` 전체 텍스트를 여기 넘긴다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_manifest.py
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
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_manifest.py -v
```

Expected: FAIL (`core.manifest` 없음)

- [ ] **Step 3: 구현**

```python
# core/manifest.py
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
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_manifest.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/manifest.py tests/core/test_manifest.py
git commit -m "core: add SKILL.md manifest parsing and validation"
```

---

## Task 4: 장식자 — @tool, @on, @schedule, @prompt_context, @slash_only

**Files:**
- Create: `core/decorators.py`
- Create: `tests/core/test_decorators.py`

**Interfaces:**
- Consumes: 없음 (장식 대상 함수의 첫 인자는 `ToolContext` 타입이어야 한다는 관례만 가정)
- Produces: `ToolSpec`, `EventHandlerSpec`, `ScheduleSpec`, `PromptContextSpec`, `SlashOnlySpec` (전부 dataclass), 장식자 함수 `tool`, `on`, `schedule`, `prompt_context`, `slash_only`, `DecoratorError`, `collect_specs(module) -> SkillCode`(`tools: list[ToolSpec]`, `event_handlers: list[EventHandlerSpec]`, `schedules: list[ScheduleSpec]`, `prompt_contexts: list[PromptContextSpec]`, `slash_only_tools: list[SlashOnlySpec]`). Task 11(registry)이 `skill.py`를 import한 뒤 이 함수로 스캔한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_decorators.py
import pytest

from core.decorators import (
    DecoratorError,
    collect_specs,
    on,
    prompt_context,
    schedule,
    slash_only,
    tool,
)
from core.tool_types import ToolContext, ToolResult


@tool(slash="보더", tags=["chunithm-synced"], cost="network")
async def lookup(ctx: ToolContext, name: str) -> ToolResult:
    """곡과 난이도의 허용 어택 개수를 계산한다."""
    return ToolResult(data={"name": name})


@on("chunithm.sync.completed")
async def on_sync(ctx: ToolContext, payload: dict) -> None:
    """동기화 완료 이벤트를 받는다."""


@schedule("0 9 * * *", catch_up=True)
async def daily(ctx: ToolContext) -> None:
    """매일 9시에 돈다."""


@prompt_context(max_chars=200)
async def hint(ctx: ToolContext) -> str:
    """플레이어 맥락을 짧게 돌려준다."""
    return "맥락"


@slash_only("연습 체험시작", mutates=True)
async def start_demo(ctx: ToolContext) -> ToolResult:
    """데모 상태 태그를 부여한다."""
    return ToolResult(data={})


def test_tool_spec_attached() -> None:
    spec = lookup.__yuni_tool__
    assert spec.slash == "보더"
    assert spec.tags == ["chunithm-synced"]
    assert spec.cost == "network"
    assert spec.confirm == "never"
    assert spec.mutates is False


def test_collect_specs_groups_everything() -> None:
    import types

    module = types.ModuleType("fake_skill")
    module.lookup = lookup
    module.on_sync = on_sync
    module.daily = daily
    module.hint = hint
    module.start_demo = start_demo

    code = collect_specs(module)

    assert [spec.fn.__name__ for spec in code.tools] == ["lookup"]
    assert [spec.event_name for spec in code.event_handlers] == ["chunithm.sync.completed"]
    assert [spec.cron for spec in code.schedules] == ["0 9 * * *"]
    assert code.schedules[0].catch_up is True
    assert [spec.max_chars for spec in code.prompt_contexts] == [200]
    assert [spec.slash for spec in code.slash_only_tools] == ["연습 체험시작"]


def test_tool_requires_docstring() -> None:
    with pytest.raises(DecoratorError, match="docstring"):

        @tool(slash="실패")
        async def no_doc(ctx: ToolContext) -> ToolResult:
            return ToolResult(data={})


def test_tool_requires_tool_context_first_param() -> None:
    with pytest.raises(DecoratorError, match="ToolContext"):

        @tool(slash="실패2")
        async def bad_first_param(name: str) -> ToolResult:
            """첫 인자가 ctx가 아님."""
            return ToolResult(data={})
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_decorators.py -v
```

Expected: FAIL (`core.decorators` 없음)

- [ ] **Step 3: 구현**

```python
# core/decorators.py
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

ConfirmMode = Literal["never", "bulk", "always"]
CostTier = Literal["light", "network", "llm"]


class DecoratorError(ValueError):
    pass


@dataclass(slots=True, kw_only=True)
class ToolSpec:
    fn: Callable[..., Awaitable[Any]]
    name: str
    slash: str | None = None
    tags: list[str] = field(default_factory=list)
    tags_any: list[str] = field(default_factory=list)
    mutates: bool = False
    confirm: ConfirmMode = "never"
    cost: CostTier = "light"


@dataclass(slots=True, kw_only=True)
class SlashOnlySpec:
    fn: Callable[..., Awaitable[Any]]
    name: str
    slash: str
    tags: list[str] = field(default_factory=list)
    tags_any: list[str] = field(default_factory=list)
    mutates: bool = False
    confirm: ConfirmMode = "never"


@dataclass(slots=True, kw_only=True)
class EventHandlerSpec:
    fn: Callable[..., Awaitable[Any]]
    event_name: str


@dataclass(slots=True, kw_only=True)
class ScheduleSpec:
    fn: Callable[..., Awaitable[Any]]
    cron: str
    catch_up: bool = False


@dataclass(slots=True, kw_only=True)
class PromptContextSpec:
    fn: Callable[..., Awaitable[Any]]
    max_chars: int = 300


@dataclass(slots=True, kw_only=True)
class SkillCode:
    tools: list[ToolSpec] = field(default_factory=list)
    slash_only_tools: list[SlashOnlySpec] = field(default_factory=list)
    event_handlers: list[EventHandlerSpec] = field(default_factory=list)
    schedules: list[ScheduleSpec] = field(default_factory=list)
    prompt_contexts: list[PromptContextSpec] = field(default_factory=list)


def _require_docstring(fn: Callable[..., Any]) -> None:
    if not (fn.__doc__ and fn.__doc__.strip()):
        raise DecoratorError(f"'{fn.__name__}'에 docstring이 없다. 도구 설명은 docstring에서 나온다")


def _require_tool_context_first_param(fn: Callable[..., Any]) -> None:
    params = list(inspect.signature(fn).parameters.values())
    if not params or params[0].name != "ctx":
        raise DecoratorError(
            f"'{fn.__name__}'의 첫 인자는 ToolContext 타입의 'ctx'여야 한다"
        )


def tool(
    *,
    slash: str | None = None,
    tags: list[str] | None = None,
    tags_any: list[str] | None = None,
    mutates: bool = False,
    confirm: ConfirmMode = "never",
    cost: CostTier = "light",
) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_docstring(fn)
        _require_tool_context_first_param(fn)
        fn.__yuni_tool__ = ToolSpec(  # type: ignore[attr-defined]
            fn=fn,
            name=fn.__name__,
            slash=slash,
            tags=tags or [],
            tags_any=tags_any or [],
            mutates=mutates,
            confirm=confirm,
            cost=cost,
        )
        return fn

    return deco


def slash_only(
    slash: str,
    *,
    tags: list[str] | None = None,
    tags_any: list[str] | None = None,
    mutates: bool = False,
    confirm: ConfirmMode = "never",
) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_docstring(fn)
        _require_tool_context_first_param(fn)
        fn.__yuni_slash_only__ = SlashOnlySpec(  # type: ignore[attr-defined]
            fn=fn,
            name=fn.__name__,
            slash=slash,
            tags=tags or [],
            tags_any=tags_any or [],
            mutates=mutates,
            confirm=confirm,
        )
        return fn

    return deco


def on(event_name: str) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_tool_context_first_param(fn)
        fn.__yuni_on__ = EventHandlerSpec(fn=fn, event_name=event_name)  # type: ignore[attr-defined]
        return fn

    return deco


def schedule(cron: str, *, catch_up: bool = False) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_tool_context_first_param(fn)
        fn.__yuni_schedule__ = ScheduleSpec(fn=fn, cron=cron, catch_up=catch_up)  # type: ignore[attr-defined]
        return fn

    return deco


def prompt_context(*, max_chars: int = 300) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        _require_tool_context_first_param(fn)
        fn.__yuni_prompt_context__ = PromptContextSpec(fn=fn, max_chars=max_chars)  # type: ignore[attr-defined]
        return fn

    return deco


def collect_specs(module: Any) -> SkillCode:
    """import된 skill.py 모듈에서 장식된 함수를 모두 모은다."""
    code = SkillCode()
    for _, member in inspect.getmembers(module, inspect.iscoroutinefunction):
        if spec := getattr(member, "__yuni_tool__", None):
            code.tools.append(spec)
        if spec := getattr(member, "__yuni_slash_only__", None):
            code.slash_only_tools.append(spec)
        if spec := getattr(member, "__yuni_on__", None):
            code.event_handlers.append(spec)
        if spec := getattr(member, "__yuni_schedule__", None):
            code.schedules.append(spec)
        if spec := getattr(member, "__yuni_prompt_context__", None):
            code.prompt_contexts.append(spec)
    return code
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_decorators.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/decorators.py tests/core/test_decorators.py
git commit -m "core: add @tool/@on/@schedule/@prompt_context/@slash_only decorators"
```

---

## Task 5: 도구 인자 스키마 빌더

**Files:**
- Create: `core/schema.py`
- Create: `tests/core/test_schema.py`

**Interfaces:**
- Consumes: `core.tool_types.STANDARD_REF_TYPES`
- Produces: `ArgSpec(name, py_type, required, default, is_ref_type)`, `ArgSchema(args: list[ArgSpec])`, `SchemaBuildError`, `build_arg_schema(fn) -> ArgSchema`. Task 11(registry, 계약 테스트)과 Task 12(executor)가 이 스키마로 각각 "스키마 생성 가능 여부"와 "실제 인자 검증"을 한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_schema.py
import enum

import pytest

from core.schema import SchemaBuildError, build_arg_schema
from core.tool_types import ChartRef, ToolContext


class Difficulty(enum.Enum):
    MAS = "MAS"
    ULT = "ULT"


async def simple(ctx: ToolContext, name: str, count: int = 3) -> None: ...
async def with_ref(ctx: ToolContext, chart: ChartRef, rank: str | None = None) -> None: ...
async def with_enum(ctx: ToolContext, difficulty: Difficulty) -> None: ...
async def unsupported(ctx: ToolContext, blob: dict) -> None: ...


def test_build_arg_schema_primitives() -> None:
    schema = build_arg_schema(simple)
    assert [a.name for a in schema.args] == ["name", "count"]
    assert schema.args[0].required is True
    assert schema.args[1].required is False
    assert schema.args[1].default == 3


def test_build_arg_schema_ref_type_and_optional() -> None:
    schema = build_arg_schema(with_ref)
    chart_arg, rank_arg = schema.args
    assert chart_arg.is_ref_type is True
    assert chart_arg.py_type is ChartRef
    assert rank_arg.required is False
    assert rank_arg.is_ref_type is False


def test_build_arg_schema_enum() -> None:
    schema = build_arg_schema(with_enum)
    assert schema.args[0].py_type is Difficulty


def test_build_arg_schema_rejects_unsupported_type() -> None:
    with pytest.raises(SchemaBuildError, match="blob"):
        build_arg_schema(unsupported)
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_schema.py -v
```

Expected: FAIL (`core.schema` 없음)

- [ ] **Step 3: 구현**

```python
# core/schema.py
from __future__ import annotations

import enum
import inspect
import types
from dataclasses import dataclass, field
from typing import Any, Callable, get_type_hints

from core.tool_types import STANDARD_REF_TYPES

_PRIMITIVES: frozenset[type] = frozenset({str, int, float, bool})


class SchemaBuildError(ValueError):
    pass


@dataclass(slots=True, kw_only=True)
class ArgSpec:
    name: str
    py_type: type
    required: bool
    default: Any = None
    is_ref_type: bool = False


@dataclass(slots=True, kw_only=True)
class ArgSchema:
    args: list[ArgSpec] = field(default_factory=list)


def _unwrap_optional(py_type: Any) -> tuple[Any, bool]:
    if (
        isinstance(py_type, types.UnionType)
        or getattr(py_type, "__origin__", None) is not None
    ):
        args = getattr(py_type, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and type(None) in args:
            return non_none[0], True
    return py_type, False


def build_arg_schema(fn: Callable[..., Any]) -> ArgSchema:
    """ctx를 제외한 함수 인자에서 JSON Schema에 준하는 ArgSchema를 만든다."""
    hints = get_type_hints(fn)
    signature = inspect.signature(fn)
    args: list[ArgSpec] = []

    for param_name, param in signature.parameters.items():
        if param_name == "ctx":
            continue

        py_type = hints.get(param_name, Any)
        py_type, is_optional = _unwrap_optional(py_type)

        is_ref_type = py_type in STANDARD_REF_TYPES
        is_enum = isinstance(py_type, type) and issubclass(py_type, enum.Enum)
        is_primitive = py_type in _PRIMITIVES

        if not (is_ref_type or is_enum or is_primitive):
            raise SchemaBuildError(
                f"'{fn.__name__}'의 인자 '{param_name}'({py_type})는 지원하지 않는 타입이다. "
                "표준 인자 타입, Enum, str/int/float/bool만 쓸 수 있다"
            )

        has_default = param.default is not inspect.Parameter.empty
        args.append(
            ArgSpec(
                name=param_name,
                py_type=py_type,
                required=not (has_default or is_optional),
                default=None if param.default is inspect.Parameter.empty else param.default,
                is_ref_type=is_ref_type,
            )
        )

    return ArgSchema(args=args)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_schema.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/schema.py tests/core/test_schema.py
git commit -m "core: add tool argument schema builder"
```

---

## Task 6: 표준 인자 타입 해석기 자리

**Files:**
- Create: `core/resolvers.py`
- Create: `tests/core/test_resolvers.py`

**Interfaces:**
- Consumes: `core.tool_types.ToolContext`
- Produces: `ResolutionError(candidates: list[str])`, `Resolver` Protocol(`async def resolve(self, raw: Any, ctx: ToolContext) -> Any`), `ResolverRegistry`(`register(ref_type, resolver)`, `get(ref_type) -> Resolver`, `is_registered(ref_type) -> bool`). Task 12(executor)가 인자 해석에 쓰고, 테스트는 가짜 해석기로 확인한다(실제 해석은 3~4단계).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_resolvers.py
import pytest

from core.resolvers import ResolutionError, ResolverRegistry
from core.tool_types import ChartRef, RankRef, ToolContext


class _FakeChartResolver:
    async def resolve(self, raw: object, ctx: ToolContext) -> dict:
        if raw == "크로노":
            return {"song": "크로노", "difficulty": "MAS", "const": 14.9}
        raise ResolutionError(candidates=["크로노", "크로노스"])


def test_register_and_resolve() -> None:
    registry = ResolverRegistry()
    registry.register(ChartRef, _FakeChartResolver())

    resolver = registry.get(ChartRef)
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})

    import asyncio

    result = asyncio.run(resolver.resolve("크로노", ctx))
    assert result["const"] == 14.9


def test_resolver_not_registered_raises() -> None:
    registry = ResolverRegistry()
    with pytest.raises(KeyError, match="RankRef"):
        registry.get(RankRef)


def test_resolution_error_carries_candidates() -> None:
    err = ResolutionError(candidates=["a", "b"])
    assert err.candidates == ["a", "b"]
    assert "a" in str(err)
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_resolvers.py -v
```

Expected: FAIL (`core.resolvers` 없음)

- [ ] **Step 3: 구현**

```python
# core/resolvers.py
from __future__ import annotations

from typing import Any, Protocol

from core.tool_types import ToolContext


class ResolutionError(Exception):
    """표준 인자 타입 해석 실패. candidates가 비어 있으면 '찾지 못함'이다."""

    def __init__(self, *, candidates: list[str] | None = None, message: str | None = None) -> None:
        self.candidates = candidates or []
        if message is None:
            message = (
                f"후보: {', '.join(self.candidates)}" if self.candidates else "찾지 못함"
            )
        super().__init__(message)


class Resolver(Protocol):
    async def resolve(self, raw: Any, ctx: ToolContext) -> Any: ...


class ResolverRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[type, Resolver] = {}

    def register(self, ref_type: type, resolver: Resolver) -> None:
        self._resolvers[ref_type] = resolver

    def is_registered(self, ref_type: type) -> bool:
        return ref_type in self._resolvers

    def get(self, ref_type: type) -> Resolver:
        try:
            return self._resolvers[ref_type]
        except KeyError:
            raise KeyError(f"{ref_type.__name__}에 등록된 해석기가 없다") from None
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_resolvers.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/resolvers.py tests/core/test_resolvers.py
git commit -m "core: add standard arg type resolver registry"
```

---

## Task 7: 권한 태그 검사 공유 함수와 임시 태그 제공자

**Files:**
- Create: `core/permissions.py`
- Create: `tests/core/test_permissions.py`

**Interfaces:**
- Consumes: `core.manifest.DefinedTag`
- Produces: `TagRequirement(tags, tags_any)`, `check_tags(requirement, player_tags) -> bool`(순수 함수, 프롬프트 조립·도구 실행기·슬래시 경로가 공유), `TagProvider` Protocol(`async def tags_for(player_id) -> set[str]`), `InMemoryTagProvider`(`grant`, `revoke`, `tags_for`), `LockResult`(`visible: bool`, `message: str | None`), `describe_lock(missing_tags, tag_definitions) -> LockResult`. Task 12(executor)와 Task 13(slash)이 같은 `check_tags`/`describe_lock`을 호출한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_permissions.py
import pytest

from core.manifest import DefinedTag
from core.permissions import (
    InMemoryTagProvider,
    TagRequirement,
    check_tags,
    describe_lock,
)


def test_check_tags_all_required() -> None:
    req = TagRequirement(tags=["admin", "tester"])
    assert check_tags(req, {"admin", "tester", "extra"}) is True
    assert check_tags(req, {"admin"}) is False


def test_check_tags_any_of() -> None:
    req = TagRequirement(tags_any=["a", "b"])
    assert check_tags(req, {"b"}) is True
    assert check_tags(req, {"c"}) is False


def test_check_tags_no_requirement_passes() -> None:
    assert check_tags(TagRequirement(), set()) is True


@pytest.mark.asyncio
async def test_in_memory_tag_provider_grant_revoke() -> None:
    provider = InMemoryTagProvider()
    assert await provider.tags_for("p1") == set()

    provider.grant("p1", "chunithm-linked")
    assert await provider.tags_for("p1") == {"chunithm-linked"}

    provider.revoke("p1", "chunithm-linked")
    assert await provider.tags_for("p1") == set()


def test_describe_lock_visible_status_tag_has_guidance() -> None:
    defs = {
        "chunithm-linked": DefinedTag(
            name="chunithm-linked",
            description="CHUNITHM-NET 연동됨",
            how_to_get="/계정 연결 로그인으로 로그인하면 얻는다",
            hidden=False,
        )
    }
    lock = describe_lock(missing_tags=["chunithm-linked"], tag_definitions=defs)
    assert lock.visible is True
    assert "로그인" in (lock.message or "")


def test_describe_lock_hidden_tag_gives_no_guidance() -> None:
    defs = {
        "practice-secret": DefinedTag(
            name="practice-secret",
            description="데모용 부여 태그",
            how_to_get="관리자가 직접 부여",
            hidden=True,
        )
    }
    lock = describe_lock(missing_tags=["practice-secret"], tag_definitions=defs)
    assert lock.visible is False
    assert lock.message is None
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_permissions.py -v
```

Expected: FAIL (`core.permissions` 없음)

- [ ] **Step 3: 구현**

```python
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
    if requirement.tags_any and not (set(requirement.tags_any) & player_tags):
        return False
    return True


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
    if any(d.hidden for d in definitions) or len(definitions) != len(missing_tags):
        return LockResult(visible=False, message=None)

    hints = "; ".join(f"{d.description} ({d.how_to_get})" for d in definitions)
    return LockResult(visible=True, message=f"아직 쓸 수 없어요. {hints}")
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_permissions.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/permissions.py tests/core/test_permissions.py
git commit -m "core: add shared tag check function and in-memory tag provider"
```

---

## Task 8: 이벤트 버스

**Files:**
- Create: `core/events.py`
- Create: `tests/core/test_events.py`

**Interfaces:**
- Consumes: 없음
- Produces: `Section(order, display, data, hint, stage)`, `EventBus(gather_timeout=5.0, max_chain_depth=3)`, `bus.register_notify(event_name, owner_skill, fn)`, `bus.register_gather(event_name, owner_skill, fn)`, `await bus.emit_notify(event_name, payload)`(기다리지 않음), `await bus.emit_gather(event_name, payload) -> list[Section]`(order, skill 이름순 정렬), `await bus.drain()`(테스트용, 배경 태스크 완료 대기). Task 11(registry)이 스킬의 `@on` 핸들러를 여기 등록하고, Task 17(bot.py)이 실제 이벤트를 낸다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_events.py
import asyncio

import pytest

from core.events import EventBus, Section


@pytest.mark.asyncio
async def test_emit_notify_does_not_block_emitter() -> None:
    bus = EventBus()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_handler(payload: object) -> None:
        started.set()
        await asyncio.sleep(0.05)
        finished.set()

    bus.register_notify("players.linked", owner_skill="account-link", fn=slow_handler)

    await bus.emit_notify("players.linked", {"player_id": "1"})
    assert not finished.is_set()  # emit_notify 자체는 핸들러를 기다리지 않는다

    await bus.drain()
    assert finished.is_set()


@pytest.mark.asyncio
async def test_emit_gather_orders_sections() -> None:
    bus = EventBus()

    async def from_b(payload: object) -> Section | None:
        return Section(order=1, data={"from": "b"})

    async def from_a(payload: object) -> Section | None:
        return Section(order=1, data={"from": "a"})

    async def from_c(payload: object) -> Section | None:
        return Section(order=0, data={"from": "c"})

    bus.register_gather("chunithm.sync.completed", owner_skill="skill-b", fn=from_b)
    bus.register_gather("chunithm.sync.completed", owner_skill="skill-a", fn=from_a)
    bus.register_gather("chunithm.sync.completed", owner_skill="skill-c", fn=from_c)

    sections = await bus.emit_gather("chunithm.sync.completed", {})

    assert [s.data["from"] for s in sections] == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_emit_gather_drops_slow_handler() -> None:
    bus = EventBus(gather_timeout=0.01)

    async def slow(payload: object) -> Section | None:
        await asyncio.sleep(1)
        return Section(data={"slow": True})

    async def fast(payload: object) -> Section | None:
        return Section(data={"fast": True})

    bus.register_gather("evt", owner_skill="slow-skill", fn=slow)
    bus.register_gather("evt", owner_skill="fast-skill", fn=fast)

    sections = await bus.emit_gather("evt", {})

    assert len(sections) == 1
    assert sections[0].data == {"fast": True}


@pytest.mark.asyncio
async def test_emit_gather_handler_returning_none_is_skipped() -> None:
    bus = EventBus()

    async def nothing_to_say(payload: object) -> Section | None:
        return None

    bus.register_gather("evt", owner_skill="s", fn=nothing_to_say)
    sections = await bus.emit_gather("evt", {})
    assert sections == []


@pytest.mark.asyncio
async def test_chain_depth_limit_enforced() -> None:
    bus = EventBus(max_chain_depth=3)
    depths_seen: list[int] = []

    async def chain(payload: object) -> None:
        depths_seen.append(payload["depth"])
        await bus.emit_notify("chain", {"depth": payload["depth"] + 1})

    bus.register_notify("chain", owner_skill="s", fn=chain)
    await bus.emit_notify("chain", {"depth": 0})
    await bus.drain()

    assert depths_seen == [0, 1, 2, 3]  # 0에서 시작해 depth 3까지만 돌고 4단계(다음 체인)는 끊긴다
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_events.py -v
```

Expected: FAIL (`core.events` 없음)

- [ ] **Step 3: 구현**

```python
# core/events.py
from __future__ import annotations

import asyncio
import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

Stage = Literal["great", "good", "close", "rest"]

_chain_depth: contextvars.ContextVar[int] = contextvars.ContextVar("yuni_event_chain_depth", default=0)


@dataclass(slots=True, kw_only=True)
class Section:
    order: int = 0
    display: Any | None = None
    data: dict[str, Any] = field(default_factory=dict)
    hint: str | None = None
    stage: Stage | None = None


@dataclass(slots=True, kw_only=True)
class _Handler:
    owner_skill: str
    fn: Callable[[Any], Awaitable[Any]]


class EventBus:
    def __init__(self, *, gather_timeout: float = 5.0, max_chain_depth: int = 3) -> None:
        self._gather_timeout = gather_timeout
        self._max_chain_depth = max_chain_depth
        self._notify_handlers: dict[str, list[_Handler]] = {}
        self._gather_handlers: dict[str, list[_Handler]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def register_notify(self, event_name: str, *, owner_skill: str, fn: Callable[[Any], Awaitable[Any]]) -> None:
        self._notify_handlers.setdefault(event_name, []).append(_Handler(owner_skill=owner_skill, fn=fn))

    def register_gather(self, event_name: str, *, owner_skill: str, fn: Callable[[Any], Awaitable[Any]]) -> None:
        self._gather_handlers.setdefault(event_name, []).append(_Handler(owner_skill=owner_skill, fn=fn))

    async def emit_notify(self, event_name: str, payload: Any) -> None:
        if _chain_depth.get() > self._max_chain_depth:
            logger.warning("event chain depth exceeded, dropping %s", event_name)
            return

        for handler in self._notify_handlers.get(event_name, []):
            task = asyncio.create_task(self._run_notify_handler(handler, payload))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

    async def _run_notify_handler(self, handler: _Handler, payload: Any) -> None:
        token = _chain_depth.set(_chain_depth.get() + 1)
        try:
            await handler.fn(payload)
        except Exception:
            logger.exception("notify handler '%s' failed for owner %s", handler.fn, handler.owner_skill)
        finally:
            _chain_depth.reset(token)

    async def emit_gather(self, event_name: str, payload: Any) -> list[Section]:
        handlers = self._gather_handlers.get(event_name, [])
        if not handlers:
            return []

        token = _chain_depth.set(_chain_depth.get() + 1)
        try:
            results = await asyncio.gather(
                *(self._run_gather_handler(h, payload) for h in handlers), return_exceptions=False
            )
        finally:
            _chain_depth.reset(token)

        sections = [(owner, section) for owner, section in results if section is not None]
        sections.sort(key=lambda pair: (pair[1].order, pair[0]))
        return [section for _, section in sections]

    async def _run_gather_handler(self, handler: _Handler, payload: Any) -> tuple[str, Section | None]:
        try:
            section = await asyncio.wait_for(handler.fn(payload), timeout=self._gather_timeout)
        except TimeoutError:
            logger.warning("gather handler '%s' (%s) timed out", handler.fn, handler.owner_skill)
            return handler.owner_skill, None
        except Exception:
            logger.exception("gather handler '%s' (%s) failed", handler.fn, handler.owner_skill)
            return handler.owner_skill, None
        return handler.owner_skill, section

    async def drain(self) -> None:
        """테스트 전용: 배경에서 도는 notify 핸들러가 모두 끝나길 기다린다."""
        while self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_events.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/events.py tests/core/test_events.py
git commit -m "core: add event bus with notify/gather, timeout, and chain depth limit"
```

---

## Task 9: 대화 스레드

**Files:**
- Create: `core/threads.py`
- Create: `tests/core/test_threads.py`

**Interfaces:**
- Consumes: 없음
- Produces: `ThreadMessage(player_id, content, at)`, `ThreadTranscript(thread_id, player_id, messages, closed_reason)`, `ThreadTransport` Protocol(`async def send_message(text)`), `ThreadManager`(`open(...) -> ThreadSession`, `receive(thread_id, player_id, content)`, `check_idle(now)`, `close(thread_id, reason)`).실제 스킬(세션 후기 등)은 7/12단계에서 이 API로 스레드를 연다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_threads.py
from datetime import datetime, timedelta, timezone

import pytest

from core.threads import ThreadManager, ThreadTranscript

UTC = timezone.utc


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, text: str) -> None:
        self.sent.append(text)


@pytest.mark.asyncio
async def test_receive_appends_transcript_and_resets_idle_timer() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="chunithm-session",
        player_id="p1",
        transport=transport,
        idle_timeout=300.0,
        close_timeout=300.0,
        on_close=lambda t: closed.append(t),
        now=base,
    )

    await manager.receive(session.thread_id, "p1", "오늘 세션 어땠어", now=base + timedelta(seconds=10))
    assert session.transcript[0].content == "오늘 세션 어땠어"

    # 10초 뒤에도 idle_timeout(300초)에 한참 못 미치므로 체크해도 아직 살아 있다
    await manager.check_idle(now=base + timedelta(seconds=20))
    assert transport.sent == []
    assert closed == []


@pytest.mark.asyncio
async def test_idle_timeout_asks_then_closes() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="chunithm-session",
        player_id="p1",
        transport=transport,
        idle_timeout=300.0,
        close_timeout=300.0,
        on_close=lambda t: closed.append(t),
        now=base,
    )

    await manager.check_idle(now=base + timedelta(seconds=301))
    assert len(transport.sent) == 1  # 무응답 확인 메시지

    await manager.check_idle(now=base + timedelta(seconds=301 + 301))
    assert len(closed) == 1
    assert closed[0].closed_reason == "timeout"


@pytest.mark.asyncio
async def test_message_during_check_phase_cancels_close() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="chunithm-session",
        player_id="p1",
        transport=transport,
        idle_timeout=300.0,
        close_timeout=300.0,
        on_close=lambda t: closed.append(t),
        now=base,
    )

    await manager.check_idle(now=base + timedelta(seconds=301))
    await manager.receive(session.thread_id, "p1", "아직 안 끝났어", now=base + timedelta(seconds=310))
    await manager.check_idle(now=base + timedelta(seconds=320))

    assert closed == []


@pytest.mark.asyncio
async def test_explicit_close_delivers_transcript_immediately() -> None:
    manager = ThreadManager()
    transport = _FakeTransport()
    closed: list[ThreadTranscript] = []

    base = datetime(2026, 1, 1, tzinfo=UTC)
    session = await manager.open(
        skill_name="chunithm-session",
        player_id="p1",
        transport=transport,
        on_close=lambda t: closed.append(t),
        now=base,
    )
    await manager.receive(session.thread_id, "p1", "끝낼게", now=base)
    await manager.close(session.thread_id, reason="explicit")

    assert len(closed) == 1
    assert closed[0].closed_reason == "explicit"
    assert closed[0].messages[0].content == "끝낼게"
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_threads.py -v
```

Expected: FAIL (`core.threads` 없음)

- [ ] **Step 3: 구현**

```python
# core/threads.py
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Literal, Protocol

ClosedReason = Literal["explicit", "timeout"]


class ThreadTransport(Protocol):
    async def send_message(self, text: str) -> None: ...


@dataclass(slots=True, kw_only=True)
class ThreadMessage:
    player_id: str
    content: str
    at: datetime


@dataclass(slots=True, kw_only=True)
class ThreadTranscript:
    thread_id: str
    player_id: str
    messages: list[ThreadMessage]
    closed_reason: ClosedReason


@dataclass(slots=True, kw_only=True)
class ThreadSession:
    thread_id: str
    skill_name: str
    player_id: str
    transport: ThreadTransport
    idle_timeout: float
    close_timeout: float
    on_close: Callable[[ThreadTranscript], None | Awaitable[None]]
    transcript: list[ThreadMessage] = field(default_factory=list)
    last_activity: datetime = field(default_factory=lambda: datetime.min)
    state: Literal["open", "checking", "closed"] = "open"
    checked_at: datetime | None = None


class ThreadManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ThreadSession] = {}

    async def open(
        self,
        *,
        skill_name: str,
        player_id: str,
        transport: ThreadTransport,
        on_close: Callable[[ThreadTranscript], None | Awaitable[None]],
        idle_timeout: float = 300.0,
        close_timeout: float = 300.0,
        now: datetime,
    ) -> ThreadSession:
        session = ThreadSession(
            thread_id=str(uuid.uuid4()),
            skill_name=skill_name,
            player_id=player_id,
            transport=transport,
            idle_timeout=idle_timeout,
            close_timeout=close_timeout,
            on_close=on_close,
            last_activity=now,
        )
        self._sessions[session.thread_id] = session
        return session

    async def receive(self, thread_id: str, player_id: str, content: str, *, now: datetime) -> None:
        session = self._sessions.get(thread_id)
        if session is None or session.state == "closed":
            return
        session.transcript.append(ThreadMessage(player_id=player_id, content=content, at=now))
        session.last_activity = now
        session.state = "open"
        session.checked_at = None

    async def check_idle(self, *, now: datetime) -> None:
        for session in list(self._sessions.values()):
            if session.state == "closed":
                continue

            if session.state == "open":
                if (now - session.last_activity).total_seconds() >= session.idle_timeout:
                    await session.transport.send_message("다 끝났나요?")
                    session.state = "checking"
                    session.checked_at = now
            elif session.state == "checking":
                assert session.checked_at is not None
                if (now - session.checked_at).total_seconds() >= session.close_timeout:
                    await self.close(session.thread_id, reason="timeout")

    async def close(self, thread_id: str, *, reason: ClosedReason) -> None:
        session = self._sessions.get(thread_id)
        if session is None or session.state == "closed":
            return
        session.state = "closed"
        transcript = ThreadTranscript(
            thread_id=session.thread_id,
            player_id=session.player_id,
            messages=list(session.transcript),
            closed_reason=reason,
        )
        result = session.on_close(transcript)
        if result is not None:
            await result
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_threads.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/threads.py tests/core/test_threads.py
git commit -m "core: add conversation thread manager with idle check-in and auto-close"
```

---

## Task 10: SQL 마이그레이션 실행기

**Files:**
- Create: `core/migrations.py`
- Create: `tests/core/test_migrations.py`

**Interfaces:**
- Consumes: `aiosqlite.Connection`
- Produces: `MigrationError`, `async def run_migrations(conn, *, roots: list[tuple[str, Path]]) -> list[str]`(`roots`는 `(소유자_이름, 디렉터리)` 튜플 목록이고 호출자가 이미 "코어 먼저, 스킬은 이름순"으로 정렬해서 넘긴다; 반환값은 이번 호출에서 새로 적용한 `"소유자/파일명"` 목록). `schema_migrations(owner, filename, checksum, applied_at)` 테이블을 이 모듈이 부트스트랩한다. Task 17(bot.py)과 Task 11(registry, 스킬 디렉터리 정렬)이 이 함수를 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_migrations.py
from pathlib import Path

import aiosqlite
import pytest

from core.migrations import MigrationError, run_migrations


@pytest.mark.asyncio
async def test_run_migrations_applies_in_order_and_records(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "001_init.sql").write_text("CREATE TABLE players (id TEXT PRIMARY KEY);")

    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    (skill_dir / "001_init.sql").write_text("CREATE TABLE demo_notes (id INTEGER PRIMARY KEY);")

    async with aiosqlite.connect(":memory:") as conn:
        applied = await run_migrations(conn, roots=[("core", core_dir), ("demo", skill_dir)])
        assert applied == ["core/001_init.sql", "demo/001_init.sql"]

        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
        assert {"players", "demo_notes", "schema_migrations"} <= tables


@pytest.mark.asyncio
async def test_run_migrations_is_idempotent(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "001_init.sql").write_text("CREATE TABLE players (id TEXT PRIMARY KEY);")

    async with aiosqlite.connect(":memory:") as conn:
        first = await run_migrations(conn, roots=[("core", core_dir)])
        second = await run_migrations(conn, roots=[("core", core_dir)])
        assert first == ["core/001_init.sql"]
        assert second == []


@pytest.mark.asyncio
async def test_run_migrations_rolls_back_failed_file(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "001_bad.sql").write_text(
        "CREATE TABLE ok_table (id INTEGER); THIS IS NOT SQL;"
    )

    async with aiosqlite.connect(":memory:") as conn:
        with pytest.raises(MigrationError):
            await run_migrations(conn, roots=[("core", core_dir)])

        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in await cursor.fetchall()}
        assert "ok_table" not in tables


@pytest.mark.asyncio
async def test_run_migrations_detects_changed_applied_file(tmp_path: Path) -> None:
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    migration_file = core_dir / "001_init.sql"
    migration_file.write_text("CREATE TABLE players (id TEXT PRIMARY KEY);")

    async with aiosqlite.connect(":memory:") as conn:
        await run_migrations(conn, roots=[("core", core_dir)])

        migration_file.write_text("CREATE TABLE players (id TEXT PRIMARY KEY, nickname TEXT);")

        with pytest.raises(MigrationError, match="바뀌었다"):
            await run_migrations(conn, roots=[("core", core_dir)])
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_migrations.py -v
```

Expected: FAIL (`core.migrations` 없음)

- [ ] **Step 3: 구현**

```python
# core/migrations.py
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    owner TEXT NOT NULL,
    filename TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (owner, filename)
);
"""


class MigrationError(RuntimeError):
    pass


async def run_migrations(conn: aiosqlite.Connection, *, roots: list[tuple[str, Path]]) -> list[str]:
    """roots는 호출자가 이미 적용 순서(코어 먼저, 스킬은 이름순)로 정렬해 넘긴다."""
    await conn.execute(_BOOTSTRAP_SQL)
    await conn.commit()

    applied: list[str] = []
    for owner, root in roots:
        if not root.is_dir():
            continue
        for sql_file in sorted(root.glob("*.sql")):
            applied_name = await _apply_one(conn, owner=owner, sql_file=sql_file)
            if applied_name:
                applied.append(applied_name)
    return applied


async def _apply_one(conn: aiosqlite.Connection, *, owner: str, sql_file: Path) -> str | None:
    content = sql_file.read_text()
    checksum = hashlib.sha256(content.encode()).hexdigest()

    cursor = await conn.execute(
        "SELECT checksum FROM schema_migrations WHERE owner = ? AND filename = ?",
        (owner, sql_file.name),
    )
    row = await cursor.fetchone()
    if row is not None:
        if row[0] != checksum:
            raise MigrationError(
                f"이미 적용한 마이그레이션 파일이 바뀌었다: {owner}/{sql_file.name}"
            )
        return None

    try:
        await conn.executescript(content)
        await conn.execute(
            "INSERT INTO schema_migrations (owner, filename, checksum, applied_at) VALUES (?, ?, ?, ?)",
            (owner, sql_file.name, checksum, datetime.now(UTC).isoformat()),
        )
        await conn.commit()
    except Exception as exc:
        await conn.rollback()
        raise MigrationError(f"마이그레이션 실패: {owner}/{sql_file.name}: {exc}") from exc

    return f"{owner}/{sql_file.name}"
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_migrations.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/migrations.py tests/core/test_migrations.py
git commit -m "core: add SQL migration runner with checksum guard for applied files"
```

---

## Task 11: 스킬 레지스트리, `_template/`, 연습/불량 스킬 픽스처

이 Task가 가장 크다. 레지스트리, `skills/_template/`, 그리고 레지스트리 테스트가 쓰는 픽스처 스킬들을 한 번에 만든다(픽스처 없이는 레지스트리를 테스트할 수 없고, 레지스트리 없이는 픽스처가 의미가 없기 때문).

**Files:**
- Create: `core/registry.py`
- Create: `skills/_template/SKILL.md`
- Create: `skills/_template/skill.py`
- Create: `tests/fixtures/skills_ok/practice/SKILL.md`
- Create: `tests/fixtures/skills_ok/practice/skill.py`
- Create: `tests/fixtures/skills_ok/practice/migrations/001_init.sql`
- Create: `tests/fixtures/skills_bad/missing_skill_md/skill.py` (SKILL.md 없음)
- Create: `tests/fixtures/skills_bad/bad_category/SKILL.md`, `skill.py`
- Create: `tests/fixtures/skills_bad/undeclared_service/SKILL.md`, `skill.py`
- Create: `tests/fixtures/skills_bad/unknown_listen/SKILL.md`, `skill.py`
- Create: `tests/fixtures/skills_bad/undefined_tag/SKILL.md`, `skill.py`
- Create: `tests/fixtures/skills_bad/slash_collision_standalone/SKILL.md`, `skill.py`
- Create: `tests/fixtures/skills_bad/slash_collision_group/SKILL.md`, `skill.py`
- Create: `tests/core/test_registry.py`

**Interfaces:**
- Consumes: `core.manifest.parse_manifest`, `core.decorators.collect_specs`, `core.schema.build_arg_schema`
- Produces: `LoadedSkill(manifest, path, code)`, `RegistryFailure(skill_id, reason)`, `SkillRegistry(skill_dirs: list[Path])`(`load()`, `enabled_skills() -> list[LoadedSkill]`, `failures() -> dict[str, str]`, `tag_definitions() -> dict[str, DefinedTag]`, `tool_specs() -> list[tuple[LoadedSkill, ToolSpec | SlashOnlySpec]]`). Task 12(executor), Task 13(slash), Task 17(bot.py)이 `SkillRegistry`를 쓴다.

- [ ] **Step 1: `skills/_template/` 작성 (복사용 원본, 레지스트리가 스캔하지 않도록 밑줄 접두사)**

```markdown
<!-- skills/_template/SKILL.md -->
# 유니 스킬: _template

새 스킬은 이 폴더를 복사해서 만든다. `_`로 시작하는 폴더는 레지스트리가 스캔하지 않는다.

## 개요

```yaml
name: my-skill
description: 무엇을 하고 언제 쓰는지 200자 이내로.
examples:
  - 이 스킬을 골라야 하는 질문 예시 1
  - 질문 예시 2
category: 메모
game: common
version: 1
services: []
---
```

## SKILL.md 본문

펼칠 때만 모델에 들어가는 지침을 여기에 쓴다.
```

```python
# skills/_template/skill.py
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="템플릿")
async def example(ctx: ToolContext, name: str) -> ToolResult:
    """예시 도구. 복사한 뒤 지우거나 바꾼다."""
    return ToolResult(data={"name": name})
```

(주: `_template`의 SKILL.md는 설명용 문서이지 실제 매니페스트가 아니다. 레지스트리가 `_`로 시작하는 디렉터리를 건너뛰므로 내용 형식은 자유롭다. 다만 복사해서 바로 쓸 수 있도록 실제 프런트매터 형식(`---`로 감싼 YAML)으로 작성해 둔다 — 위 코드블록의 `---`가 열림/닫힘 역할을 한다.)

- [ ] **Step 2: 연습용(정상) 픽스처 스킬 작성**

```markdown
<!-- tests/fixtures/skills_ok/practice/SKILL.md -->
---
name: practice
description: 2단계 코어 뼈대를 수동으로 확인하기 위한 연습용 스킬. 배포하지 않는다.
examples:
  - 연습 스킬 테스트
category: 메모
game: common
version: 1
services: []
defines_tags:
  - name: practice-unlocked
    description: 연습 체험을 시작한 플레이어
    how_to_get: "/연습 체험시작 실행"
    hidden: false
  - name: practice-secret
    description: 데모용 부여 태그 흉내(완전히 숨겨짐)
    how_to_get: 관리자가 FakeTagProvider에 직접 부여(데모 전용)
    hidden: true
---

이 스킬은 2단계 코어 통과 기준을 수동으로 확인하는 용도다. `/연습 체험시작`으로 상태 태그를 얻고,
`/연습 보기`는 그 태그가 있어야 쓸 수 있다. `/연습 비밀`은 완전히 숨겨진 부여 태그 데모다.
```

```python
# tests/fixtures/skills_ok/practice/skill.py
from core.decorators import slash_only, tool
from core.tool_types import ToolContext, ToolResult


@slash_only("연습 체험시작")
async def start_demo(ctx: ToolContext) -> ToolResult:
    """practice-unlocked 상태 태그를 흉내 낸다. 실제 부여는 bot.py가 InMemoryTagProvider로 한다."""
    return ToolResult(data={"granted": "practice-unlocked"})


@tool(slash="연습 보기", tags=["practice-unlocked"])
async def view_demo(ctx: ToolContext) -> ToolResult:
    """체험을 시작한 플레이어만 볼 수 있는 연습용 도구."""
    return ToolResult(data={"message": "연습 콘텐츠를 볼 수 있다"})


@tool(slash="연습 비밀", tags=["practice-secret"])
async def secret_demo(ctx: ToolContext) -> ToolResult:
    """완전히 숨겨지는 부여 태그 데모."""
    return ToolResult(data={"message": "숨겨진 콘텐츠"})


@tool(slash="연습 초기화", mutates=True, confirm="always")
async def reset_demo(ctx: ToolContext) -> ToolResult:
    """확인 버튼 플로우를 보여주는 데모. 실제로 바꾸는 상태는 없다."""
    return ToolResult(data={"reset": True})
```

```sql
-- tests/fixtures/skills_ok/practice/migrations/001_init.sql
CREATE TABLE practice_demo_counter (
    player_id TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);
```

- [ ] **Step 3: 불량 픽스처 스킬 7종 작성 (각각 레지스트리가 잡아야 할 결함 하나씩)**

```python
# tests/fixtures/skills_bad/missing_skill_md/skill.py
# SKILL.md가 아예 없다.
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함1")
async def broken(ctx: ToolContext) -> ToolResult:
    """SKILL.md가 없는 스킬."""
    return ToolResult(data={})
```

```markdown
<!-- tests/fixtures/skills_bad/bad_category/SKILL.md -->
---
name: bad-category
description: 잘못된 category 값을 쓰는 스킬.
examples: [예시]
category: 없는분류
game: common
version: 1
services: []
---
본문
```

```python
# tests/fixtures/skills_bad/bad_category/skill.py
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함2")
async def broken(ctx: ToolContext) -> ToolResult:
    """잘못된 category."""
    return ToolResult(data={})
```

```markdown
<!-- tests/fixtures/skills_bad/undeclared_service/SKILL.md -->
---
name: undeclared-service
description: manifest에 없는 서비스를 코드에서 쓰는 스킬.
examples: [예시]
category: 메모
game: common
version: 1
services: []
---
본문
```

```python
# tests/fixtures/skills_bad/undeclared_service/skill.py
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함3")
async def broken(ctx: ToolContext) -> ToolResult:
    """services에 songdb를 선언하지 않았는데 ctx.songdb를 쓴다."""
    ctx.songdb.lookup("크로노")
    return ToolResult(data={})
```

```markdown
<!-- tests/fixtures/skills_bad/unknown_listen/SKILL.md -->
---
name: unknown-listen
description: 아무도 내지 않는 이벤트를 구독하는 스킬.
examples: [예시]
category: 메모
game: common
version: 1
services: []
listens: [no.such.event]
---
본문
```

```python
# tests/fixtures/skills_bad/unknown_listen/skill.py
from core.decorators import on
from core.tool_types import ToolContext


@on("no.such.event")
async def handle(ctx: ToolContext, payload: dict) -> None:
    """아무도 내지 않는 이벤트."""
```

```markdown
<!-- tests/fixtures/skills_bad/undefined_tag/SKILL.md -->
---
name: undefined-tag
description: 정의되지 않은 태그를 조건으로 쓰는 스킬.
examples: [예시]
category: 메모
game: common
version: 1
services: []
---
본문
```

```python
# tests/fixtures/skills_bad/undefined_tag/skill.py
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함4", tags=["no-such-tag"])
async def broken(ctx: ToolContext) -> ToolResult:
    """정의되지 않은 태그."""
    return ToolResult(data={})
```

```markdown
<!-- tests/fixtures/skills_bad/slash_collision_standalone/SKILL.md -->
---
name: slash-collision-standalone
description: "연습" 하나를 단독 명령으로 쓴다.
examples: [예시]
category: 메모
game: common
version: 1
services: []
---
본문
```

```python
# tests/fixtures/skills_bad/slash_collision_standalone/skill.py
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="연습")
async def standalone(ctx: ToolContext) -> ToolResult:
    """"연습"을 단독 명령으로 등록한다."""
    return ToolResult(data={})
```

```markdown
<!-- tests/fixtures/skills_bad/slash_collision_group/SKILL.md -->
---
name: slash-collision-group
description: "연습 충돌"을 하위 명령으로 써서 "연습"을 그룹으로 만든다.
examples: [예시]
category: 메모
game: common
version: 1
services: []
---
본문
```

```python
# tests/fixtures/skills_bad/slash_collision_group/skill.py
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="연습 충돌")
async def sub(ctx: ToolContext) -> ToolResult:
    """"연습"을 그룹으로 요구하는 하위 명령. slash_collision_standalone과 충돌한다."""
    return ToolResult(data={})
```

- [ ] **Step 4: 실패하는 레지스트리 테스트 작성**

```python
# tests/core/test_registry.py
from pathlib import Path

from core.registry import SkillRegistry

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_valid_skill_loads() -> None:
    registry = SkillRegistry([FIXTURES / "skills_ok"])
    registry.load()

    names = {s.manifest.name for s in registry.enabled_skills()}
    assert "practice" in names
    assert registry.failures() == {}


def test_template_dir_is_skipped() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()

    names = {s.manifest.name for s in registry.enabled_skills()}
    assert "_template" not in names
    assert "my-skill" not in names  # _template 안의 placeholder manifest.name


def test_missing_skill_md_is_disabled_with_reason() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "missing_skill_md"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "SKILL.md" in reason


def test_bad_category_is_disabled() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "bad_category"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "category" in reason


def test_undeclared_service_usage_is_disabled() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "undeclared_service"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "songdb" in reason


def test_unknown_listen_is_disabled() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "unknown_listen"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "no.such.event" in reason


def test_undefined_tag_is_disabled() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "undefined_tag"])
    registry.load()
    assert registry.enabled_skills() == []
    [reason] = registry.failures().values()
    assert "no-such-tag" in reason


def test_slash_collision_disables_later_skill_only() -> None:
    registry = SkillRegistry(
        [
            FIXTURES / "skills_bad" / "slash_collision_standalone",
            FIXTURES / "skills_bad" / "slash_collision_group",
        ]
    )
    registry.load()

    enabled_names = {s.manifest.name for s in registry.enabled_skills()}
    assert enabled_names == {"slash-collision-standalone"}
    [reason] = registry.failures().values()
    assert "연습" in reason


def test_one_broken_skill_does_not_disable_valid_sibling() -> None:
    registry = SkillRegistry([FIXTURES / "skills_ok", FIXTURES / "skills_bad" / "bad_category"])
    registry.load()

    enabled_names = {s.manifest.name for s in registry.enabled_skills()}
    assert "practice" in enabled_names
    assert len(registry.failures()) == 1
```

- [ ] **Step 5: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_registry.py -v
```

Expected: FAIL (`core.registry` 없음)

- [ ] **Step 6: 구현**

```python
# core/registry.py
from __future__ import annotations

import ast
import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path

from core.decorators import SkillCode, SlashOnlySpec, ToolSpec, collect_specs
from core.manifest import DefinedTag, ManifestError, SkillManifest, parse_manifest
from core.schema import SchemaBuildError, build_arg_schema

logger = logging.getLogger(__name__)

KNOWN_SERVICE_NAMES: frozenset[str] = frozenset(
    {
        "players", "settings", "memory", "notify", "yuni", "secrets", "arcades",
        "websearch", "http", "chunithm_net", "songdb", "records", "terms",
    }
)

# 00-core.md "이벤트 목록"에서 코어/서비스가 내는 것으로 적힌 이벤트.
# 3~4단계 전까지는 실제로 내는 코드가 없어도 "정의된 이벤트"로 취급한다.
CORE_EMITTED_EVENTS: frozenset[str] = frozenset(
    {"players.first_seen", "chunithm.login.expired"}
)

CORE_DEFINED_TAGS: frozenset[str] = frozenset({"admin", "tester", "chunithm-linked", "chunithm-synced"})


@dataclass(slots=True, kw_only=True)
class LoadedSkill:
    manifest: SkillManifest
    path: Path
    code: SkillCode


class SkillRegistry:
    def __init__(self, skill_dirs: list[Path]) -> None:
        self._skill_dirs = skill_dirs
        self._loaded: dict[str, LoadedSkill] = {}
        self._failures: dict[str, str] = {}

    def load(self) -> None:
        self._loaded.clear()
        self._failures.clear()

        candidates = self._discover_dirs()
        parsed: list[tuple[Path, SkillManifest, SkillCode]] = []

        for skill_dir in candidates:
            try:
                manifest, code = self._load_one(skill_dir)
            except (ManifestError, SchemaBuildError, _SkillLoadError) as exc:
                self._failures[skill_dir.name] = str(exc)
                continue
            parsed.append((skill_dir, manifest, code))

        all_tag_defs = self._collect_tag_definitions(parsed)
        all_emits = self._collect_emitted_events(parsed)

        for skill_dir, manifest, code in parsed:
            if manifest.name in self._loaded:
                self._failures[skill_dir.name] = (
                    f"스킬 이름 '{manifest.name}'이 이미 등록된 스킬과 겹친다"
                )
                continue

            reason = self._validate_cross_skill(manifest, code, all_tag_defs, all_emits)
            if reason:
                self._failures[skill_dir.name] = reason
                continue

            self._loaded[manifest.name] = LoadedSkill(manifest=manifest, path=skill_dir, code=code)

        self._detect_slash_collisions()

    def enabled_skills(self) -> list[LoadedSkill]:
        return list(self._loaded.values())

    def failures(self) -> dict[str, str]:
        return dict(self._failures)

    def tag_definitions(self) -> dict[str, DefinedTag]:
        return self._collect_tag_definitions(
            [(s.path, s.manifest, s.code) for s in self._loaded.values()]
        )

    def tool_specs(self) -> list[tuple[LoadedSkill, ToolSpec | SlashOnlySpec]]:
        specs: list[tuple[LoadedSkill, ToolSpec | SlashOnlySpec]] = []
        for skill in self._loaded.values():
            for spec in [*skill.code.tools, *skill.code.slash_only_tools]:
                specs.append((skill, spec))
        return specs

    def _discover_dirs(self) -> list[Path]:
        # 각 root는 "스킬 여러 개를 담은 디렉터리"(skills/ 처럼) 또는 "스킬 디렉터리 그 자체"
        # 둘 다로 쓸 수 있다. 후자는 픽스처 테스트에서 개별 스킬 하나를 직접 가리킬 때 쓴다.
        dirs: list[Path] = []
        for root in self._skill_dirs:
            if not root.is_dir():
                continue
            if (root / "SKILL.md").is_file() or (root / "skill.py").is_file():
                dirs.append(root)
                continue
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and not entry.name.startswith("_"):
                    dirs.append(entry)
        return dirs

    def _load_one(self, skill_dir: Path) -> tuple[SkillManifest, SkillCode]:
        manifest_path = skill_dir / "SKILL.md"
        if not manifest_path.is_file():
            raise _SkillLoadError(f"'{skill_dir}'에 SKILL.md가 없다")

        manifest = parse_manifest(manifest_path.read_text())

        skill_py = skill_dir / "skill.py"
        if not skill_py.is_file():
            raise _SkillLoadError(f"'{skill_dir}'에 skill.py가 없다")

        undeclared = _find_undeclared_service_usage(skill_py, declared=set(manifest.services))
        if undeclared:
            raise _SkillLoadError(
                f"선언하지 않은 서비스를 쓴다: {', '.join(sorted(undeclared))} "
                f"(manifest.services에 추가해야 한다)"
            )

        module = _import_skill_module(skill_py, module_name=f"_yuni_skill_{manifest.name.replace('-', '_')}")
        code = collect_specs(module)

        for spec in [*code.tools, *code.slash_only_tools]:
            build_arg_schema(spec.fn)  # 스키마 생성 불가면 SchemaBuildError 전파

        declared_events = {h.event_name for h in code.event_handlers}
        undeclared_listens = declared_events - set(manifest.listens)
        if undeclared_listens:
            raise _SkillLoadError(
                f"manifest.listens에 없는 이벤트를 구독한다: {', '.join(sorted(undeclared_listens))}"
            )

        return manifest, code

    def _validate_cross_skill(
        self,
        manifest: SkillManifest,
        code: SkillCode,
        all_tag_defs: dict[str, DefinedTag],
        all_emits: frozenset[str],
    ) -> str | None:
        used_tags: set[str] = set()
        for spec in [*code.tools, *code.slash_only_tools]:
            used_tags |= set(spec.tags) | set(spec.tags_any)
        used_tags |= set(manifest.tags) | set(manifest.tags_any)

        known_tags = CORE_DEFINED_TAGS | set(all_tag_defs)
        unknown = used_tags - known_tags
        if unknown:
            return f"정의되지 않은 태그를 조건으로 쓴다: {', '.join(sorted(unknown))}"

        unknown_listens = set(manifest.listens) - all_emits
        if unknown_listens:
            return f"아무도 내지 않는 이벤트를 구독한다: {', '.join(sorted(unknown_listens))}"

        return None

    def _collect_tag_definitions(
        self, parsed: list[tuple[Path, SkillManifest, SkillCode]]
    ) -> dict[str, DefinedTag]:
        defs: dict[str, DefinedTag] = {}
        for _, manifest, _ in parsed:
            for tag in manifest.defines_tags:
                defs[tag.name] = tag
        return defs

    def _collect_emitted_events(
        self, parsed: list[tuple[Path, SkillManifest, SkillCode]]
    ) -> frozenset[str]:
        emitted = set(CORE_EMITTED_EVENTS)
        for _, manifest, _ in parsed:
            emitted |= set(manifest.emits)
        return frozenset(emitted)

    def _detect_slash_collisions(self) -> None:
        # 토큰 단위로 경로를 모아, "단독 명령으로 쓴 이름"과 "그룹(하위 명령 보유)으로 쓴 이름"이
        # 겹치는지, 완전히 같은 경로가 두 번 쓰였는지를 검사한다.
        path_owner: dict[tuple[str, ...], str] = {}
        root_is_group: dict[str, bool] = {}
        root_owner: dict[str, str] = {}

        for skill_name in list(self._loaded):
            skill = self._loaded[skill_name]
            paths = [
                tuple(spec.slash.split())
                for spec in [*skill.code.tools, *skill.code.slash_only_tools]
                if getattr(spec, "slash", None)
            ]
            for path in paths:
                root = path[0]
                is_group = len(path) > 1

                if root in root_owner and root_owner[root] != skill_name:
                    if root_is_group[root] != is_group:
                        self._disable(
                            skill_name,
                            f"슬래시 명령 '{root}'이 단독 명령과 그룹으로 동시에 쓰였다 "
                            f"(이미 '{root_owner[root]}' 스킬이 다른 형태로 등록함)",
                        )
                        break
                else:
                    root_owner.setdefault(root, skill_name)
                    root_is_group.setdefault(root, is_group)

                if path in path_owner and path_owner[path] != skill_name:
                    self._disable(
                        skill_name,
                        f"슬래시 경로 '{' '.join(path)}'가 '{path_owner[path]}' 스킬과 겹친다",
                    )
                    break
                path_owner.setdefault(path, skill_name)

    def _disable(self, skill_name: str, reason: str) -> None:
        skill = self._loaded.pop(skill_name, None)
        self._failures[skill.path.name if skill else skill_name] = reason


class _SkillLoadError(Exception):
    pass


def _import_skill_module(skill_py: Path, *, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, skill_py)
    if spec is None or spec.loader is None:
        raise _SkillLoadError(f"'{skill_py}'를 import할 수 없다")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_undeclared_service_usage(skill_py: Path, *, declared: set[str]) -> set[str]:
    """skill.py를 파싱해 'ctx.<서비스>' 접근 중 manifest.services에 없는 것을 찾는다."""
    tree = ast.parse(skill_py.read_text(), filename=str(skill_py))
    undeclared: set[str] = set()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "ctx"
            and node.attr in KNOWN_SERVICE_NAMES
            and node.attr not in declared
        ):
            undeclared.add(node.attr)

    return undeclared
```

- [ ] **Step 7: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_registry.py -v
```

Expected: PASS (9 tests)

- [ ] **Step 8: 커밋**

```bash
git add core/registry.py skills/_template tests/fixtures tests/core/test_registry.py
git commit -m "core: add skill registry with manifest/code validation, _template, practice fixtures"
```

---

## Task 12: 도구 실행기

**Files:**
- Create: `core/executor.py`
- Create: `tests/core/test_executor.py`

**Interfaces:**
- Consumes: `core.schema.build_arg_schema`, `core.resolvers.ResolverRegistry`, `core.permissions.{check_tags, describe_lock, TagRequirement, TagProvider}`, `core.decorators.ToolSpec`, `core.tool_types.{ToolContext, ToolResult}`
- Produces: `ExecutionOutcome(result: ToolResult | None, needs_confirm: bool, confirm_token: str | None)`, `ToolExecutor(resolvers, tag_provider, tag_definitions)`(`await invoke(spec, ctx, raw_args, *, skill_tags=None, skill_tags_any=None) -> ExecutionOutcome`, `await confirm(token, *, confirmed: bool) -> ToolResult`, 읽기 전용 프로퍼티 `tag_provider`). Task 13(slash)이 이 실행기를 통해 모든 슬래시 호출을 처리하고, Task 15(bot.py)가 `executor.tag_provider`로 디스코드 클라이언트와 같은 태그 제공자를 공유한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_executor.py
import pytest

from core.decorators import tool
from core.executor import ToolExecutor
from core.manifest import DefinedTag
from core.permissions import InMemoryTagProvider
from core.resolvers import ResolutionError, ResolverRegistry
from core.tool_types import ChartRef, ToolContext, ToolResult


@tool(slash="보더")
async def lookup(ctx: ToolContext, chart: ChartRef, count: int = 1) -> ToolResult:
    """채보 기준 계산."""
    return ToolResult(data={"chart": chart, "count": count})


@tool(slash="보더잠김", tags=["chunithm-linked"])
async def locked(ctx: ToolContext) -> ToolResult:
    """상태 태그가 필요한 도구."""
    return ToolResult(data={})


@tool(slash="비밀도구", tags=["practice-secret"])
async def hidden_tool(ctx: ToolContext) -> ToolResult:
    """숨김 태그가 필요한 도구."""
    return ToolResult(data={})


@tool(slash="위험", mutates=True, confirm="always")
async def dangerous(ctx: ToolContext) -> ToolResult:
    """확인이 필요한 도구."""
    return ToolResult(data={"done": True})


@tool(slash="터짐")
async def boom(ctx: ToolContext) -> ToolResult:
    """예외를 던지는 도구."""
    raise RuntimeError("oops")


class _FakeChartResolver:
    async def resolve(self, raw: object, ctx: ToolContext) -> dict:
        if raw == "크로노":
            return {"song": "크로노"}
        raise ResolutionError(candidates=["크로노"])


def _make_executor() -> ToolExecutor:
    resolvers = ResolverRegistry()
    resolvers.register(ChartRef, _FakeChartResolver())
    tag_provider = InMemoryTagProvider()
    tag_defs = {
        "chunithm-linked": DefinedTag(
            name="chunithm-linked", description="연동됨", how_to_get="로그인하면 얻는다", hidden=False
        ),
        "practice-secret": DefinedTag(
            name="practice-secret", description="비밀", how_to_get="관리자 부여", hidden=True
        ),
    }
    return ToolExecutor(resolvers=resolvers, tag_provider=tag_provider, tag_definitions=tag_defs), tag_provider


@pytest.mark.asyncio
async def test_invoke_happy_path() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(lookup.__yuni_tool__, ctx, {"chart": "크로노", "count": 2})

    assert outcome.needs_confirm is False
    assert outcome.result is not None
    assert outcome.result.data["count"] == 2
    assert outcome.result.data["chart"]["song"] == "크로노"


@pytest.mark.asyncio
async def test_invoke_resolution_failure_returns_error() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(lookup.__yuni_tool__, ctx, {"chart": "없는곡"})

    assert outcome.result is not None
    assert outcome.result.error is not None
    assert "크로노" in outcome.result.error


@pytest.mark.asyncio
async def test_invoke_missing_visible_tag_gives_guidance() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(locked.__yuni_tool__, ctx, {})

    assert outcome.result is not None
    assert "로그인" in (outcome.result.error or "")


@pytest.mark.asyncio
async def test_invoke_missing_hidden_tag_gives_generic_error() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(hidden_tool.__yuni_tool__, ctx, {})

    assert outcome.result is not None
    assert "로그인" not in (outcome.result.error or "")
    assert "비밀" not in (outcome.result.error or "")


@pytest.mark.asyncio
async def test_invoke_with_satisfied_tag_succeeds() -> None:
    executor, tag_provider = _make_executor()
    tag_provider.grant("p1", "chunithm-linked")
    tags = await tag_provider.tags_for("p1")
    ctx = ToolContext(player_id="p1", tags=frozenset(tags), services={})

    outcome = await executor.invoke(locked.__yuni_tool__, ctx, {})

    assert outcome.result is not None
    assert outcome.result.error is None


@pytest.mark.asyncio
async def test_invoke_confirm_flow() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(dangerous.__yuni_tool__, ctx, {})
    assert outcome.needs_confirm is True
    assert outcome.result is None
    assert outcome.confirm_token is not None

    result = await executor.confirm(outcome.confirm_token, confirmed=True)
    assert result.data == {"done": True}


@pytest.mark.asyncio
async def test_invoke_confirm_declined_does_not_run() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(dangerous.__yuni_tool__, ctx, {})
    result = await executor.confirm(outcome.confirm_token, confirmed=False)
    assert result.error is not None


@pytest.mark.asyncio
async def test_invoke_exception_becomes_error_result() -> None:
    executor, _ = _make_executor()
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={})

    outcome = await executor.invoke(boom.__yuni_tool__, ctx, {})

    assert outcome.result is not None
    assert "oops" in (outcome.result.error or "")
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_executor.py -v
```

Expected: FAIL (`core.executor` 없음)

- [ ] **Step 3: 구현**

```python
# core/executor.py
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from core.decorators import SlashOnlySpec, ToolSpec
from core.manifest import DefinedTag
from core.permissions import TagProvider, TagRequirement, check_tags, describe_lock
from core.resolvers import ResolutionError, ResolverRegistry
from core.schema import build_arg_schema
from core.tool_types import ToolContext, ToolResult

logger = logging.getLogger(__name__)


@dataclass(slots=True, kw_only=True)
class ExecutionOutcome:
    result: ToolResult | None
    needs_confirm: bool = False
    confirm_token: str | None = None


@dataclass(slots=True, kw_only=True)
class _PendingConfirm:
    spec: ToolSpec | SlashOnlySpec
    ctx: ToolContext
    resolved_args: dict[str, Any]


class ToolExecutor:
    def __init__(
        self,
        *,
        resolvers: ResolverRegistry,
        tag_provider: TagProvider,
        tag_definitions: dict[str, DefinedTag],
    ) -> None:
        self._resolvers = resolvers
        self._tag_provider = tag_provider
        self._tag_definitions = tag_definitions
        self._pending: dict[str, _PendingConfirm] = {}

    @property
    def tag_provider(self) -> TagProvider:
        """슬래시 콜백(core/slash.py)이 실행기와 같은 태그 제공자를 공유하기 위해 쓴다."""
        return self._tag_provider

    async def invoke(
        self,
        spec: ToolSpec | SlashOnlySpec,
        ctx: ToolContext,
        raw_args: dict[str, Any],
        *,
        skill_tags: list[str] | None = None,
        skill_tags_any: list[str] | None = None,
    ) -> ExecutionOutcome:
        """skill_tags/skill_tags_any는 이 도구가 속한 스킬 manifest의 조건이다.
        도구 자신의 tags/tags_any에 더해진다("스킬의 tags에 더해지는 조건")."""
        schema = build_arg_schema(spec.fn)

        resolved: dict[str, Any] = {}
        for arg in schema.args:
            if arg.name not in raw_args:
                if arg.required:
                    return ExecutionOutcome(result=ToolResult(error=f"필수 인자 '{arg.name}'가 없다"))
                resolved[arg.name] = arg.default
                continue

            raw_value = raw_args[arg.name]
            if arg.is_ref_type:
                try:
                    resolver = self._resolvers.get(arg.py_type)
                    resolved[arg.name] = await resolver.resolve(raw_value, ctx)
                except ResolutionError as exc:
                    return ExecutionOutcome(result=ToolResult(error=str(exc)))
            else:
                resolved[arg.name] = raw_value

        effective_tags = list(spec.tags) + list(skill_tags or [])
        effective_tags_any = list(spec.tags_any) + list(skill_tags_any or [])
        requirement = TagRequirement(tags=effective_tags, tags_any=effective_tags_any)
        player_tags = set(ctx.tags)
        if not check_tags(requirement, player_tags):
            missing_all = set(effective_tags) - player_tags
            missing_any: set[str] = set()
            if effective_tags_any and not (set(effective_tags_any) & player_tags):
                missing_any = set(effective_tags_any)
            lock = describe_lock(
                missing_tags=sorted(missing_all | missing_any), tag_definitions=self._tag_definitions
            )
            if lock.visible and lock.message:
                return ExecutionOutcome(result=ToolResult(error=lock.message))
            return ExecutionOutcome(result=ToolResult(error="모르는 명령이에요"))

        confirm_mode = getattr(spec, "confirm", "never")
        if confirm_mode == "always":
            token = str(uuid.uuid4())
            self._pending[token] = _PendingConfirm(spec=spec, ctx=ctx, resolved_args=resolved)
            return ExecutionOutcome(result=None, needs_confirm=True, confirm_token=token)

        return ExecutionOutcome(result=await self._call(spec, ctx, resolved))

    async def confirm(self, confirm_token: str, *, confirmed: bool) -> ToolResult:
        pending = self._pending.pop(confirm_token, None)
        if pending is None:
            return ToolResult(error="만료되었거나 모르는 확인 요청이다")
        if not confirmed:
            return ToolResult(error="취소했다")
        return await self._call(pending.spec, pending.ctx, pending.resolved_args)

    async def _call(
        self, spec: ToolSpec | SlashOnlySpec, ctx: ToolContext, resolved_args: dict[str, Any]
    ) -> ToolResult:
        try:
            return await spec.fn(ctx, **resolved_args)
        except Exception as exc:
            logger.exception("tool '%s' raised", spec.name)
            return ToolResult(error=f"도구 실행 중 오류: {exc}")
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_executor.py -v
```

Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/executor.py tests/core/test_executor.py
git commit -m "core: add tool executor with arg resolution, tag gating, and confirm flow"
```

---

## Task 13: 슬래시 명령 트리 빌더

**Files:**
- Create: `core/slash.py`
- Create: `tests/core/test_slash.py`

**Interfaces:**
- Consumes: `core.registry.{SkillRegistry, LoadedSkill}`, `core.decorators.{ToolSpec, SlashOnlySpec}`, `core.executor.ToolExecutor`
- Produces: `SlashPlanError`, `build_slash_plan(specs: list[tuple[LoadedSkill, ToolSpec | SlashOnlySpec]]) -> SlashPlan`(discord 의존 없는 순수 트리 검증/구조화), `attach_to_tree(tree: discord.app_commands.CommandTree, plan: SlashPlan, *, executor, tag_provider) -> None`(discord.py 의존). Task 17(bot.py)이 `attach_to_tree`를 부른다.

- [ ] **Step 1: 실패하는 테스트 작성 (순수 플랜 로직 먼저)**

```python
# tests/core/test_slash.py
import discord
import pytest

from core.decorators import tool
from core.slash import SlashPlanError, build_slash_plan
from core.tool_types import ToolContext, ToolResult


@tool(slash="보더")
async def standalone(ctx: ToolContext) -> ToolResult:
    """단독 명령."""
    return ToolResult(data={})


@tool(slash="연결 시작")
async def sub1(ctx: ToolContext) -> ToolResult:
    """2단계 하위 명령."""
    return ToolResult(data={})


@tool(slash="관리 스킬 목록")
async def sub2(ctx: ToolContext) -> ToolResult:
    """3단계(그룹+하위) 명령."""
    return ToolResult(data={})


class _FakeSkill:
    def __init__(self, name: str) -> None:
        self.manifest = type("M", (), {"name": name, "tags": [], "tags_any": []})()


def test_build_plan_standalone_and_subcommands() -> None:
    plan = build_slash_plan(
        [
            (_FakeSkill("border"), standalone.__yuni_tool__),
            (_FakeSkill("account-link"), sub1.__yuni_tool__),
            (_FakeSkill("admin"), sub2.__yuni_tool__),
        ]
    )

    paths = {tuple(entry.path) for entry in plan.entries}
    assert ("보더",) in paths
    assert ("연결", "시작") in paths
    assert ("관리", "스킬", "목록") in paths


def test_build_plan_rejects_more_than_three_levels() -> None:
    @tool(slash="너무 깊은 명령 경로 넷")
    async def too_deep(ctx: ToolContext) -> ToolResult:
        """4단계 깊이."""
        return ToolResult(data={})

    with pytest.raises(SlashPlanError, match="3단계"):
        build_slash_plan([(_FakeSkill("x"), too_deep.__yuni_tool__)])


def test_attach_to_tree_builds_offline_command(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.resolvers import ResolverRegistry
    from core.slash import attach_to_tree

    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)
    plan = build_slash_plan([(_FakeSkill("border"), standalone.__yuni_tool__)])
    executor = ToolExecutor(resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={})

    attach_to_tree(tree, plan, executor=executor)

    assert tree.get_command("보더") is not None
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_slash.py -v
```

Expected: FAIL (`core.slash` 없음)

- [ ] **Step 3: 구현**

```python
# core/slash.py
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from core.decorators import SlashOnlySpec, ToolSpec
from core.executor import ToolExecutor
from core.schema import build_arg_schema
from core.tool_types import STANDARD_REF_TYPES, ToolContext

if TYPE_CHECKING:
    from core.registry import LoadedSkill


class SlashPlanError(ValueError):
    pass


@dataclass(slots=True, kw_only=True)
class SlashPlanEntry:
    path: tuple[str, ...]
    skill_name: str
    skill_tags: list[str]
    skill_tags_any: list[str]
    spec: ToolSpec | SlashOnlySpec


@dataclass(slots=True, kw_only=True)
class SlashPlan:
    entries: list[SlashPlanEntry]


def build_slash_plan(specs: list[tuple[Any, ToolSpec | SlashOnlySpec]]) -> SlashPlan:
    entries: list[SlashPlanEntry] = []
    for skill, spec in specs:
        if not getattr(spec, "slash", None):
            continue
        path = tuple(spec.slash.split())
        if len(path) > 3:
            raise SlashPlanError(f"슬래시 경로는 최대 3단계다: '{spec.slash}'")
        entries.append(
            SlashPlanEntry(
                path=path,
                skill_name=skill.manifest.name,
                skill_tags=list(getattr(skill.manifest, "tags", [])),
                skill_tags_any=list(getattr(skill.manifest, "tags_any", [])),
                spec=spec,
            )
        )
    return SlashPlan(entries=entries)


def attach_to_tree(tree: app_commands.CommandTree, plan: SlashPlan, *, executor: ToolExecutor) -> None:
    groups: dict[tuple[str, ...], app_commands.Group] = {}

    def _get_group(path: tuple[str, ...]) -> app_commands.Group:
        if path in groups:
            return groups[path]
        group = app_commands.Group(name=path[-1], description=f"{'/'.join(path)} 묶음")
        if len(path) == 1:
            tree.add_command(group)
        else:
            parent = _get_group(path[:-1])
            parent.add_command(group)
        groups[path] = group
        return group

    for entry in plan.entries:
        command = _build_command(entry, executor=executor)
        if len(entry.path) == 1:
            tree.add_command(command)
        else:
            parent = _get_group(entry.path[:-1])
            parent.add_command(command)


def _build_command(entry: SlashPlanEntry, *, executor: ToolExecutor) -> app_commands.Command[Any, Any, Any]:
    schema = build_arg_schema(entry.spec.fn)

    async def callback(interaction: discord.Interaction, **options: Any) -> None:
        player_id = str(interaction.user.id)
        tags: frozenset[str] = frozenset()
        if hasattr(interaction.client, "tag_provider"):
            tags = frozenset(await interaction.client.tag_provider.tags_for(player_id))  # type: ignore[attr-defined]

        ctx = ToolContext(player_id=player_id, tags=tags, services={})
        outcome = await executor.invoke(
            entry.spec, ctx, options, skill_tags=entry.skill_tags, skill_tags_any=entry.skill_tags_any
        )

        if outcome.needs_confirm:
            await interaction.response.send_message(
                "확인해 주세요.", view=_ConfirmView(executor=executor, token=outcome.confirm_token or "")
            )
            return

        result = outcome.result
        assert result is not None
        if result.error:
            await interaction.response.send_message(result.error, ephemeral=True)
            return

        display = result.display
        await interaction.response.send_message(
            content=display.content if display else None,
            embeds=display.embeds if display else [],
            view=display.view if display else None,
        )

    callback.__name__ = f"slash_{entry.skill_name}_{entry.spec.name}"
    annotations: dict[str, Any] = {"interaction": discord.Interaction, "return": None}
    for arg in schema.args:
        annotations[arg.name] = str if (arg.is_ref_type or _is_enum(arg.py_type)) else arg.py_type
    callback.__annotations__ = annotations
    callback.__doc__ = entry.spec.fn.__doc__

    return app_commands.Command(
        name=entry.path[-1],
        description=(entry.spec.fn.__doc__ or "").strip().splitlines()[0][:100],
        callback=callback,
    )


def _is_enum(py_type: Any) -> bool:
    return isinstance(py_type, type) and issubclass(py_type, enum.Enum)


class _ConfirmView(discord.ui.View):
    def __init__(self, *, executor: ToolExecutor, token: str) -> None:
        super().__init__(timeout=120)
        self._executor = executor
        self._token = token

    @discord.ui.button(label="확인", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        result = await self._executor.confirm(self._token, confirmed=True)
        await interaction.response.send_message(result.error or "완료했다.")

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        result = await self._executor.confirm(self._token, confirmed=False)
        await interaction.response.send_message(result.error or "취소했다.")
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_slash.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add core/slash.py tests/core/test_slash.py
git commit -m "core: build discord slash command tree from registered tool specs"
```

---

## Task 14: 계약 테스트 모음

이전 Task들에서 이미 각 모듈 단위의 계약 요소(매니페스트 검증, 스키마 생성, 이름/슬래시 중복, 미선언 서비스, 정의 없는 태그, 마이그레이션 실패/체크섬)를 테스트했다. 이 Task는 CLAUDE.md/00-core.md가 명시한 **"테이블 접두사"**와 **"마이그레이션 파일 이름 규칙"**, 그리고 **"docstring"** 검사를 전체 레지스트리 대상으로 한 번 더 모아, "계약 테스트"라는 이름의 단일 진입점으로 정리한다.

**Files:**
- Create: `core/contract_checks.py`
- Create: `tests/core/test_contract.py`
- Create: `tests/fixtures/skills_bad/bad_table_prefix/SKILL.md`, `skill.py`, `migrations/001_init.sql`
- Create: `tests/fixtures/skills_bad/bad_migration_filename/SKILL.md`, `skill.py`, `migrations/init.sql` (번호 없음)

**Interfaces:**
- Consumes: `core.registry.LoadedSkill`
- Produces: `check_table_prefix(skill: LoadedSkill) -> list[str]`, `check_migration_filenames(skill: LoadedSkill) -> list[str]`, `check_docstrings(skill: LoadedSkill) -> list[str]`, `run_all_contract_checks(registry: SkillRegistry) -> dict[str, list[str]]`(스킬 이름 -> 위반 목록, 비어 있으면 통과).

- [ ] **Step 1: 불량 픽스처 2종 추가**

```markdown
<!-- tests/fixtures/skills_bad/bad_table_prefix/SKILL.md -->
---
name: bad-table-prefix
description: 테이블 접두사가 스킬 이름과 맞지 않는 스킬.
examples: [예시]
category: 메모
game: common
version: 1
services: []
---
본문
```

```python
# tests/fixtures/skills_bad/bad_table_prefix/skill.py
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함5")
async def broken(ctx: ToolContext) -> ToolResult:
    """테이블 접두사 위반 데모."""
    return ToolResult(data={})
```

```sql
-- tests/fixtures/skills_bad/bad_table_prefix/migrations/001_init.sql
CREATE TABLE totally_unrelated_table (id INTEGER PRIMARY KEY);
```

```markdown
<!-- tests/fixtures/skills_bad/bad_migration_filename/SKILL.md -->
---
name: bad-migration-filename
description: 마이그레이션 파일 이름 규칙을 어기는 스킬.
examples: [예시]
category: 메모
game: common
version: 1
services: []
---
본문
```

```python
# tests/fixtures/skills_bad/bad_migration_filename/skill.py
from core.decorators import tool
from core.tool_types import ToolContext, ToolResult


@tool(slash="망함6")
async def broken(ctx: ToolContext) -> ToolResult:
    """마이그레이션 파일 이름 위반 데모."""
    return ToolResult(data={})
```

```sql
-- tests/fixtures/skills_bad/bad_migration_filename/migrations/init.sql
CREATE TABLE bad_migration_filename_x (id INTEGER PRIMARY KEY);
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/core/test_contract.py
from pathlib import Path

from core.contract_checks import run_all_contract_checks
from core.registry import SkillRegistry

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_valid_skill_has_no_contract_violations() -> None:
    registry = SkillRegistry([FIXTURES / "skills_ok"])
    registry.load()

    violations = run_all_contract_checks(registry)
    assert violations.get("practice", []) == []


def test_bad_table_prefix_is_flagged() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "bad_table_prefix"])
    registry.load()
    assert registry.enabled_skills(), "레지스트리 자체는 통과해야 계약 테스트가 접두사를 잡을 수 있다"

    violations = run_all_contract_checks(registry)
    assert any("접두사" in v for v in violations["bad-table-prefix"])


def test_bad_migration_filename_is_flagged() -> None:
    registry = SkillRegistry([FIXTURES / "skills_bad" / "bad_migration_filename"])
    registry.load()

    violations = run_all_contract_checks(registry)
    assert any("파일 이름" in v for v in violations["bad-migration-filename"])


def test_template_skill_is_excluded_from_registry_and_checks() -> None:
    registry = SkillRegistry([Path("skills")])
    registry.load()
    violations = run_all_contract_checks(registry)
    assert "_template" not in violations
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_contract.py -v
```

Expected: FAIL (`core.contract_checks` 없음)

- [ ] **Step 4: 구현**

```python
# core/contract_checks.py
from __future__ import annotations

import re

from core.registry import LoadedSkill, SkillRegistry


def _expected_table_prefix(skill_name: str) -> str:
    stripped = skill_name.removeprefix("chunithm-")
    return stripped.replace("-", "_") + "_"


def check_table_prefix(skill: LoadedSkill) -> list[str]:
    violations: list[str] = []
    expected = _expected_table_prefix(skill.manifest.name)
    migrations_dir = skill.path / "migrations"
    if not migrations_dir.is_dir():
        return violations

    for sql_file in migrations_dir.glob("*.sql"):
        for match in re.finditer(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?([a-zA-Z0-9_]+)", sql_file.read_text(), re.IGNORECASE):
            table_name = match.group(1)
            if not table_name.startswith(expected):
                violations.append(
                    f"{sql_file.name}의 테이블 '{table_name}'이 접두사 '{expected}'로 시작하지 않는다"
                )
    return violations


_FILENAME_RE = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")


def check_migration_filenames(skill: LoadedSkill) -> list[str]:
    violations: list[str] = []
    migrations_dir = skill.path / "migrations"
    if not migrations_dir.is_dir():
        return violations

    for sql_file in migrations_dir.glob("*.sql"):
        if not _FILENAME_RE.match(sql_file.name):
            violations.append(
                f"마이그레이션 파일 이름이 'NNN_설명.sql' 규칙을 따르지 않는다: {sql_file.name}"
            )
    return violations


def check_docstrings(skill: LoadedSkill) -> list[str]:
    violations: list[str] = []
    for spec in [*skill.code.tools, *skill.code.slash_only_tools]:
        if not (spec.fn.__doc__ and spec.fn.__doc__.strip()):
            violations.append(f"'{spec.name}'에 docstring이 없다")
    return violations


def run_all_contract_checks(registry: SkillRegistry) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for skill in registry.enabled_skills():
        violations: list[str] = []
        violations += check_table_prefix(skill)
        violations += check_migration_filenames(skill)
        violations += check_docstrings(skill)
        results[skill.manifest.name] = violations
    return results
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_contract.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 6: 커밋**

```bash
git add core/contract_checks.py tests/core/test_contract.py tests/fixtures/skills_bad/bad_table_prefix tests/fixtures/skills_bad/bad_migration_filename
git commit -m "core: add table-prefix, migration-filename, and docstring contract checks"
```

---

## Task 15: 봇 엔트리포인트 (수동 디스코드 검증용)

**Files:**
- Create: `core/bot.py`
- Modify: `Dockerfile` (CMD를 placeholder에서 실제 엔트리포인트로)
- Modify: `migrations/core/` (빈 디렉터리 생성 — 코어 전용 SQL은 아직 없다)
- Create: `tests/core/test_bot_wiring.py` (discord 네트워크 없이 조립만 확인)

**Interfaces:**
- Consumes: Task 1~14의 모든 공개 인터페이스
- Produces: `async def build_client(config: CoreConfig) -> tuple[discord.Client, SkillRegistry]`(네트워크 연결 전 조립만, 테스트 가능), `async def main() -> None`(`build_client` 뒤 `client.start(...)` 호출, 실제 실행용).

- [ ] **Step 1: 실패하는 조립 테스트 작성**

```python
# tests/core/test_bot_wiring.py
from pathlib import Path

import pytest

from core.bot import build_client
from core.config import CoreConfig


@pytest.mark.asyncio
async def test_build_client_registers_practice_commands(tmp_path: Path) -> None:
    config = CoreConfig(
        discord_token="fake-token",
        skills_dirs=[Path("tests/fixtures/skills_ok")],
        db_path=tmp_path / "test.db",
        test_guild_id=None,
    )

    client, registry = await build_client(config)

    assert "practice" in {s.manifest.name for s in registry.enabled_skills()}
    assert client.tree.get_command("연습") is not None  # 그룹: "연습 보기" 등

    await client.close()
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_bot_wiring.py -v
```

Expected: FAIL (`core.bot` 없음)

- [ ] **Step 3: 구현**

```python
# core/bot.py
from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands

from core.config import CoreConfig, load_config
from core.executor import ToolExecutor
from core.migrations import run_migrations
from core.permissions import InMemoryTagProvider, TagProvider
from core.registry import SkillRegistry
from core.resolvers import ResolverRegistry
from core.slash import attach_to_tree, build_slash_plan

logger = logging.getLogger(__name__)


class YuniClient(discord.Client):
    def __init__(self, *, intents: discord.Intents, test_guild_id: int | None) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.tag_provider: TagProvider  # build_client()이 실행기와 같은 인스턴스로 채운다
        self._test_guild_id = test_guild_id

    async def setup_hook(self) -> None:
        if self._test_guild_id:
            guild = discord.Object(id=self._test_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


async def build_client(config: CoreConfig) -> tuple[YuniClient, SkillRegistry]:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)

    registry = SkillRegistry(config.skills_dirs)
    registry.load()
    for skill_dir, reason in registry.failures().items():
        logger.warning("skill disabled: %s (%s)", skill_dir, reason)

    conn = await aiosqlite.connect(config.db_path)
    skill_roots = sorted(
        (skill.manifest.name, skill.path / "migrations") for skill in registry.enabled_skills()
    )
    await run_migrations(conn, roots=[("core", Path("migrations/core")), *skill_roots])

    resolvers = ResolverRegistry()
    executor = ToolExecutor(
        resolvers=resolvers,
        tag_provider=InMemoryTagProvider(),
        tag_definitions=registry.tag_definitions(),
    )

    intents = discord.Intents.default()
    intents.message_content = True
    client = YuniClient(intents=intents, test_guild_id=config.test_guild_id)
    client.tag_provider = executor.tag_provider  # 실행기와 같은 임시 태그 제공자를 공유한다

    plan = build_slash_plan(registry.tool_specs())
    attach_to_tree(client.tree, plan, executor=executor)

    return client, registry


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    client, _registry = await build_client(config)
    await client.start(config.discord_token)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_bot_wiring.py -v
```

Expected: PASS (1 test)

- [ ] **Step 5: `migrations/core/` 디렉터리와 Dockerfile CMD 갱신**

```bash
mkdir -p migrations/core
touch migrations/core/.gitkeep
```

```dockerfile
# Dockerfile 마지막 줄만 교체
# 기존:
# CMD ["sleep", "infinity"]
# 교체:
CMD ["uv", "run", "python", "-m", "core.bot"]
```

- [ ] **Step 6: 전체 테스트 스위트 + 린트 + 타입 검사**

```bash
uv run pytest -v
uv run ruff check .
uv run basedpyright
```

Expected: 전부 통과(실패 시 원인을 고치고 다시 실행).

- [ ] **Step 7: 커밋**

```bash
git add core/bot.py Dockerfile migrations/core/.gitkeep tests/core/test_bot_wiring.py
git commit -m "core: add discord bot entrypoint wiring registry, migrations, executor, and slash tree"
```

---

## Task 16: 수동 디스코드 검증

코드 변경 없음 — 통과 기준을 실제로 확인하는 절차만 수행하고, 결과를 기록한다.

**Files:**
- Modify: `.env` (로컬 전용 값 채우기 — 커밋되지 않음)

- [ ] **Step 1: 테스트용 디스코드 봇 준비**

Discord 개발자 포털에서 애플리케이션/봇을 만들고(이미 있다면 재사용), `MESSAGE CONTENT INTENT`를 켠다. 봇을 지인 테스트 서버에 `applications.commands` + `bot` 스코프로 초대한다.

- [ ] **Step 2: `.env` 채우기**

```bash
# .env (커밋 안 됨)
DISCORD_TOKEN=실제_봇_토큰
SKILLS_DIRS=skills,tests/fixtures/skills_ok
TEST_GUILD_ID=테스트_서버_길드_ID
DB_PATH=data/yuni.db
```

- [ ] **Step 3: 로컬 실행**

```bash
uv run python -m core.bot
```

로그에 `skill disabled: ...` 경고가 없어야 한다(있다면 `practice` 픽스처가 아닌 다른 경로를 잘못 가리킨 것). `logged in as ...`가 뜨면 성공.

- [ ] **Step 4: 통과 기준 1 확인 — 코어를 고치지 않고 추가한 연습 스킬이 슬래시로 동작**

테스트 서버에서 `/연습 체험시작`을 실행한다. 이어서 `/연습 초기화`를 실행하면 "확인"/"취소" 버튼이 뜬다 — "확인"을 누르면 완료 메시지가 와야 한다(도구 실행기의 `confirm=always` 플로우).

- [ ] **Step 5: 통과 기준 2 확인 — 상태 태그 막힘/안내, 부여 태그 숨김**

`/연습 체험시작`을 아직 실행하지 않은 상태에서 `/연습 보기`를 실행하면 "아직 쓸 수 없어요. 연습 체험을 시작한 플레이어 (/연습 체험시작 실행)" 같은 안내가 와야 한다. `/연습 체험시작` 실행 뒤 다시 `/연습 보기`를 하면 성공 결과가 와야 한다. `/연습 비밀`은 태그가 있든 없든 안내문 없이 "모르는 명령이에요"만 돌아와야 한다(이번 단계는 Discord 명령 피커 자체에서 숨기지 않는다 — 합의된 단순화).

- [ ] **Step 6: 통과 기준 3 확인 — 규격에 어긋난 스킬은 등록되지 않음**

`SKILLS_DIRS`에 `tests/fixtures/skills_bad/bad_category`를 추가하고(쉼표로 구분) 봇을 재시작한다. 시작 로그에 `skill disabled: bad_category (...category...)`가 찍히고, 봇은 정상적으로 뜨며 `/연습 보기` 등 기존 명령은 그대로 동작해야 한다.

- [ ] **Step 7: 통과 기준 4 확인 — 테스트/린트/타입 검사**

```bash
uv run pytest -v
uv run ruff check .
uv run basedpyright
```

모두 통과해야 한다. (이 커맨드들은 Task 15 Step 6에서 이미 돌렸지만, 수동 검증 보고서에 결과를 다시 포함한다.)

- [ ] **Step 8: 확인 결과를 PR/보고에 기록**

위 2~7단계에서 실제로 실행한 명령과 받은 응답(스크린샷 또는 로그 발췌)을 작업 완료 보고에 포함한다. 코드 변경이 없으므로 git 커밋은 없다.
