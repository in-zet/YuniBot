# 3단계: 공통 서비스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2단계 코어 뼈대 위에 공통 서비스(`players`, `settings`, `memory`, `secrets`, `notify`, `arcades`, `http`)와 예약 저장, `yuni` 서비스 뼈대를 올리고, `account-link`·`admin` 두 스킬로 실제 동작을 검증한다. 끝나면 두 디스코드 계정을 하나의 플레이어로 연결할 수 있고, `.env`의 `ADMIN_DISCORD_IDS`로 지정한 계정만 `admin` 스킬을 쓸 수 있으며, 봇을 재시작해도 예약이 남고, clal 쿠키급 비밀값의 암호화 왕복이 되고 복호화된 값이 서비스 밖으로 안 나가는 것이 테스트로 확인된다.

**Architecture:** 서비스는 모두 `services/common/`에 두고, 코어가 봇 시작 시 하나씩 만들어 `ToolExecutor`에 `services: Mapping[str, object]`로 조립해 넘긴다. `core/slash.py`의 `_dispatch`는 더 이상 `discord_id`를 바로 `player_id`로 쓰지 않고, `players` 서비스로 식별 플레이어를 구하고(처음이면 `players.first_seen`을 낸다) 그 `player_id`로 `ToolContext`를 만든다. `ToolContext`에 `guild_id`(서버별 스킬 켜기/끄기 검사용)와 `event_bus`(스킬이 이벤트를 내는 통로, `ctx.emit(...)`)를 더한다. 2단계의 `InMemoryTagProvider`는 `players` 서비스로 완전히 대체된다. 2단계에서 "3단계 players 서비스가 생기면 없앤다"고 주석으로 표시해 둔 `core/slash.py`의 데모 태그 부여 장치(`DEMO_GRANT_TAG_KEY`)는 이번 단계에서 실제로 없앤다 — 그 자리를 `admin` 스킬의 `grant_tag`/`revoke_tag`가 대신한다.

`players` 서비스는 `TagProvider` 프로토콜(2단계)을 구현해 `ToolExecutor`의 태그 제공자 자리에 그대로 들어간다. `secrets`·`memory`·`settings`·`notify`·`arcades`는 각자의 SQL 마이그레이션(`migrations/core/00N_*.sql`)과 `aiosqlite.Connection`을 생성자로 받는 평범한 클래스다. `http`는 벤더링된 `third_party/chuni_penguin`이 이미 요구하는 `hishel`(캐시)·`aiolimiter`(호스트별 속도 제한) 조합을 그대로 재사용하되, 자체 SQLite 캐시 파일(`data/http_cache.sqlite`)을 코어 DB와 분리해 쓴다(코어 DB의 `schema_migrations` 관리 대상이 아닌, hishel 자신이 관리하는 캐시 스키마라서다). 예약 저장(`core/scheduler.py`)은 코어 소속이다(00-core.md의 "이벤트 버스와 예약 작업"은 서비스 표가 아니라 코어 표에 있다) — `@schedule` 데코레이터가 붙은 함수 자체는 매 기동마다 레지스트리가 다시 찾아내므로, DB에는 "다음 실행 시각/마지막 실행 시각" 같은 상태만 `(owner_skill, fn_name)` 키로 저장한다. `yuni` 서비스는 2단계 `core/events.py`의 `Section`(order/display/data/hint/stage)을 그대로 입력으로 받아 가장 높은 `stage`의 정해진 문구 하나를 돌려주는 순수 클래스다.

`account-link`와 `admin` 두 스킬은 `skills/_template/`을 복사해 만들고, 각자의 `migrations/`와 `tests/`를 갖는다. `account-link`는 `@tool` 없이 `@slash_only`만 쓰고, 연결 절차 중 "데이터를 버릴지" 확인은 이번 단계에서 만드는 코어의 범스킬 유틸리티(`core/player_data.py`)로 모든 테이블의 `player_id` 행을 세고 지운다.

**합의된 단순화 (사용자 확인 없이 진행하되, 구현 중 드러나는 세부 판단. 설계서가 정하지 않았거나 지금 단계 범위 밖인 것만 담았다):**

1. **PlayerRef 해석 범위.** 00-core.md는 PlayerRef 입력으로 "이름, 호칭, 멘션, '나'"를 든다. 이번 단계는 아직 게임 데이터도 메모리 자동 요약(호칭의 원천, 12단계)도 없으므로, 자유 텍스트 이름/별칭 매칭은 만들지 않는다. 지원하는 입력은 멘션 문자열(`<@id>`/`<@!id>`), 디스코드 스노우플레이크 숫자열, `나`/`저`/`me`뿐이다. 이 범위로도 admin의 `grant_tag` 같은 실제 쓰임을 막지 않는다(디스코드 슬래시 UI에서 대상을 멘션으로 고르면 그 텍스트가 그대로 들어온다). 자유 텍스트 매칭은 미확인 사항으로 남긴다.
2. **PlayerRef와 공개 설정.** 00-core.md 표는 PlayerRef 해석 실패로 "비공개"도 든다. 하지만 "공개 여부 검사는 도구 실행기와 조회 쿼리에서 한다"(CLAUDE.md)의 "조회 쿼리"는 아직 없는 게임별 데이터 조회 함수들의 일이다. 이번 단계의 PlayerRef 해석기는 존재 여부만 확인하고(있음/없음), 도메인별 공개 설정 검사는 그 데이터를 실제로 쓰는 미래 스킬의 몫으로 둔다. `players` 서비스는 공개 설정 플래그(기록/메모/목표/오락실/후기/동기화 리포트)를 저장·조회하는 자리만 만든다.
3. **PlayerRef로 남을 참조할 때 새 플레이어를 만들지 않는다.** 자기 자신의 식별(멘션·명령 실행)은 처음 보면 플레이어를 만들고 `players.first_seen`을 낸다. 하지만 "다른 사람"을 PlayerRef로 가리켰는데 그 디스코드 계정이 봇과 한 번도 상호작용하지 않았다면 "누구인지 모름"으로 처리한다(참조만으로 플레이어가 생기면 다섯 번 틀린 멘션 입력이 유령 플레이어를 양산한다).
4. **상태 태그와 부여 태그의 구분은 태그 이름 하나로 정한다.** `player_tags` 행에 `kind` 컬럼을 두지 않고, "상태 태그 이름 집합"(`core/permissions.py`의 `STATUS_ONLY_TAGS`, 지금은 `{"chunithm-linked", "chunithm-synced"}` — 아직 아무 서비스도 이 태그를 실제로 붙이지 않지만 이름은 2단계 레지스트리의 `CORE_DEFINED_TAGS`에 이미 있다)만으로 "손으로 못 바꾼다"를 강제한다. 어떤 스킬이 `defines_tags`로 정의한 태그는 전부 admin이 부여/회수할 수 있는 "스킬 태그"다.
5. **설정 화면 UI.** 사용자 확인: 슬래시 인자형(`/설정 보기`, `/설정 바꾸기 skill:<스킬> setting:<이름> value:<값>`)으로 만든다. 자동완성은 이번 단계에서는 만들지 않는다(입력값이 틀리면 에러 메시지로 후보를 보여준다) — discord.py의 `app_commands.autocomplete`는 코어가 아니라 각 명령 콜백에 등록해야 해서, 매니페스트 기반 동적 생성 콜백(2단계의 `_build_callback_source`와 같은 방식)에 자동완성까지 넣는 것은 이번 단계 범위를 넘는다. 미확인 사항으로 남긴다.
6. **secrets 암호화.** `cryptography`의 `Fernet`(AES128-CBC + HMAC-SHA256, 키 하나, 라이브러리가 nonce/MAC을 전부 관리)을 쓴다. 키는 `.env`의 `COOKIE_ENCRYPTION_KEY`(urlsafe base64 32바이트, `Fernet.generate_key()`로 만든 값)에서 읽는다. 키 회전은 만들지 않는다(설계서에 없다).
7. **http 캐시 저장 위치.** `hishel.AsyncSQLiteStorage`에 `anysqlite.connect("data/http_cache.sqlite")`로 연 별도 연결을 준다. 코어 DB(`data/yuni.db`)와 분리해, hishel이 자기 스키마를 그 파일 안에서 알아서 관리하게 한다(우리 `schema_migrations`가 관여하지 않는, 순전히 캐시 전용 파일이라 지워도 안전하다).
8. **이벤트 버스의 notify/gather 구분.** `@on` 데코레이터(2단계)는 종류를 받지 않는다. 핸들러의 반환 타입 힌트가 `Section`(또는 `Section | None`)이면 모으기(gather)로, 아니면 알림(notify)으로 등록한다(`core/events.py`에 추론 헬퍼를 더한다). 이번 단계에 실제로 쓰는 이벤트(`players.first_seen`, `players.linked`)는 둘 다 알림이라 이 추론은 지금은 간단한 경우만 타지만, 이후 스킬이 늘 때 코어를 고치지 않아도 되게 지금 만든다.
9. **account-link의 "다섯 번 실패".** 코드 자체가 틀리면(존재하지 않는 코드) 어느 발급자의 코드인지 알 수 없어 그 코드의 시도 횟수를 올릴 수 없다. 그래서 "다섯 번 실패"는 **참여를 시도한 디스코드 계정** 단위로 추적한다(`account_link_attempts` 테이블, 틀릴 때마다 +1, 맞으면 리셋). 5회가 쌓이면 그 계정은 "코드를 다시 받아 10분 뒤에 시도하라"는 안내를 받는다. 존재하는 코드를 입력했지만 확인 버튼을 안 누른 경우는 이 카운터와 무관하다(코드 만료로 자연히 처리된다).

**Tech Stack:** Python 3.14, discord.py 2.7(app_commands), aiosqlite, msgspec, `cryptography`(Fernet), `croniter`, `hishel[sqlite]==0.1.3` + `anysqlite`(이미 vendored 코드가 요구해 1단계에 깔려 있던 `hishel`에 `[sqlite]` extra만 더함), `aiolimiter`(1단계부터 있음), pytest, pytest-asyncio, ruff, basedpyright.

**Spec:** `docs/design/00-core.md`("유니 코어", "데이터와 설정", "이벤트, 예약 작업, 서비스", "데이터, 권한, 비밀 값", "비용과 운영"의 새벽 정지 시간대, "작업 순서"의 3단계), `docs/design/skills/account-link.md`, `docs/design/skills/admin.md`, `CLAUDE.md`. 2단계 결과물(`core/*.py`, `skills/_template/`, `tests/fixtures/`)이 이번 단계가 올라서는 기반이다.

## Global Constraints

- 이번 단계는 LLM, 에이전트 반복을 만들지 않는다. `yuni` 서비스는 정해진 문구만 돌려주는 임시 구현이다(11단계에서 교체, 부르는 쪽 코드는 안 바뀌게 인자/반환을 설계서의 조각 칸(`order`/`display`/`data`/`hint`/`stage`)에 맞춘다).
- `admin`의 사용량 표(`usage` 도구)는 자리만 만든다 — LLM 호출이 없으므로 집계할 대상이 없고, 빈 결과(0건)를 돌려줘도 된다. 비용 상한(`set_limits`)은 저장까지만 하고 적용(한도 초과 시 실제로 막는 것)은 6단계다.
- 츄니즘 서비스(`chunithm_net`, `songdb`, `records`, `terms`)와 `websearch`는 만들지 않는다. `arcades`는 공통 서비스이므로 만들지만, `chunithm-arcade` 스킬 자체(8단계)는 만들지 않는다. `memory`·`http`도 서비스만 만들고 그 서비스를 쓰는 전용 스킬(`memory` 스킬은 12단계)은 만들지 않는다.
- 코어는 츄니즘을 모른다. `arcades` 서비스의 방문 기록은 `game` 문자열 컬럼을 받되 그 값이 무엇인지는 모른다(검사하지 않는다).
- SQLite + aiosqlite. ORM/alembic 없음. 쿼리는 SQL 문 직접 작성. 코어 마이그레이션은 `migrations/core/NNN_설명.sql`로 번호순 추가, 이미 적용한 파일은 고치지 않는다.
- 스킬(`account-link`) 테이블 접두사는 스킬 이름 그대로 하이픈을 밑줄로 바꾼 것 + `_`(`account_link_`). `chunithm-` 접두사가 없으니 그대로 쓴다.
- 권한 태그 검사는 프롬프트 조립, 도구 실행기, 슬래시 경로가 같은 함수(`core.permissions.check_tags`, 2단계에서 이미 있음)를 쓴다 — 이번 단계는 바뀌지 않는다. 새로 생기는 "서버별 스킬 켜기/끄기" 검사는 태그 검사와 별도 축이라 `check_tags`를 건드리지 않고 `core/executor.py`에 새 단계로 더한다.
- 상태 태그(`chunithm-linked`, `chunithm-synced`)는 어떤 도구로도 손으로 못 바꾼다 — admin의 `grant_tag`/`revoke_tag`가 이 두 이름을 거부한다.
- 디스코드 계정 연결·해제는 `account-link`의 슬래시 명령과 버튼으로만 한다. `@tool`을 두지 않는다(LLM이 못 건드린다).
- clal 쿠키 같은 비밀값의 복호화는 `secrets` 서비스 안에서만 하고, 결과는 서비스 밖으로 안 나간다. 이번 단계는 아직 clal 쿠키를 실제로 쓰는 코드(`chunithm_net`)가 없으므로, `secrets` 서비스의 암호화 왕복과 "복호화 결과가 로그에 안 남는다"는 서비스 자체의 단위 테스트로 확인한다.
- `.env`, `data/`는 커밋하지 않는다(이미 `.gitignore`에 있음). 새 환경변수(`ADMIN_DISCORD_IDS`, `QUIET_HOURS_START`, `QUIET_HOURS_END`, `COOKIE_ENCRYPTION_KEY`)는 `.env.example`에 플레이스홀더로 추가한다(실제 `.env`는 로컬에서만 채운다).
- `ruff`/`basedpyright`는 `third_party/`를 보지 않는다(이미 설정됨). 새 코드는 `services/common/`, `core/`, `skills/account-link/`, `skills/admin/`, `tests/`에만 추가한다.

## Review Focus

- **연결 중 취소하면 정말 아무것도 안 바뀌는가**: `/연결 참여`에서 데이터 폐기 확인 버튼이 뜬 뒤 "취소"를 누르면, 참여자의 플레이어·계정·태그·메모리 어느 것도 바뀌지 않아야 한다(Task 16에서 취소 경로를 끝까지 실행해 DB 상태로 확인).
- **마지막 관리자를 못 떼는가, 그리고 env 동기화가 수동 부여를 건드리지 않는가**: `ADMIN_DISCORD_IDS`에서 어떤 계정을 빼도, 그 계정이 `admin` 스킬로 "추가"로 받은 admin 태그는 env 동기화가 회수하지 않아야 하고, admin이 1명만 남았을 때 `revoke_tag`로 그 1명의 admin을 떼려 하면 거부돼야 한다(Task 17).
- **서버별로 꺼진 스킬이 슬래시에서 정말 막히는가**: `admin`의 `toggle_skill`로 특정 서버에서 스킬을 끄면, 그 서버의 인터랙션에서는 해당 스킬의 모든 슬래시 명령이 "모르는 명령"류로 막혀야 하고, 다른 서버(또는 DM, `guild_id=None`)에는 영향이 없어야 한다(Task 9).
- **동시에 연결 시도가 오가도 코드가 중복 소비되지 않는가**: 같은 코드로 두 번 `/연결 참여`를 부르거나, 만료된 코드로 확인 버튼을 누르면 둘 다 연결이 성립하지 않고 명확한 오류를 돌려줘야 한다(Task 16).
- **새벽 정지 시간대를 걸친 catch_up이 "그날" 것만 한 번 돈다**: 예약된 실행 시각이 정지 시간대 안에 있어 밀린 경우, 켜진 뒤 그 "하루치"만 한 번 실행되고, 전원이 꺼져 있던 여러 날치가 쌓여 있어도 중복 실행되지 않아야 한다(Task 14).

---
## Task 1: 의존성 추가와 설정 확장

**Files:**
- Modify: `pyproject.toml` (의존성: `cryptography`, `croniter`, `hishel[sqlite]`)
- Modify: `core/config.py`
- Modify: `tests/core/test_config.py`
- Modify: `tests/core/test_bot_wiring.py` (`CoreConfig(...)`를 직접 만드는 두 곳에 새 필드를 채운다)
- Modify: `.env.example` (없으면 생성)

**Interfaces:**
- Consumes: 없음
- Produces: `CoreConfig`에 `admin_discord_ids: frozenset[str]`, `quiet_hours_start: datetime.time`, `quiet_hours_end: datetime.time`, `cookie_encryption_key: bytes` 필드 추가(전부 필수 필드, 기본값 없음 — 기존 필드들과 같은 모양이다). 이후 모든 Task가 이 설정으로 경로/키/시간대를 받는다.

`CoreConfig`의 네 새 필드는 기본값이 없으므로, `tests/core/test_bot_wiring.py`가 `load_config()`를 거치지 않고 `CoreConfig(...)`를 직접 만드는 두 곳(각각 `discord_token`/`skills_dirs`/`db_path`/`test_guild_id`만 채우는 생성자 호출)이 당장 깨진다. 두 곳 모두 아래 네 줄을 추가한다:

```python
        admin_discord_ids=frozenset(),
        quiet_hours_start=datetime.time(2, 0),
        quiet_hours_end=datetime.time(6, 0),
        cookie_encryption_key=b"",
```

(파일 상단에 `import datetime`이 없으면 더한다.)

- [ ] **Step 1: 의존성 추가**

```bash
cd /home/dn/projects/YuniBot
uv add cryptography croniter
uv add "hishel[sqlite]==0.1.3" --force-reinstall
```

(이미 `hishel==0.1.3`이 1단계부터 깔려 있다. `[sqlite]` extra만 더해 `anysqlite`를 받는다. 버전은 그대로 `0.1.3`으로 고정된다.)

- [ ] **Step 2: 실패하는 설정 테스트 작성**

```python
# tests/core/test_config.py 에 추가
import datetime


def test_load_config_parses_admin_ids_and_quiet_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.setenv("ADMIN_DISCORD_IDS", "111, 222 ,111")
    monkeypatch.setenv("QUIET_HOURS_START", "02:00")
    monkeypatch.setenv("QUIET_HOURS_END", "06:00")
    monkeypatch.setenv("COOKIE_ENCRYPTION_KEY", "Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9n")

    config = load_config()

    assert config.admin_discord_ids == frozenset({"111", "222"})
    assert config.quiet_hours_start == datetime.time(2, 0)
    assert config.quiet_hours_end == datetime.time(6, 0)
    assert config.cookie_encryption_key == b"Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9n"


def test_load_config_defaults_admin_ids_and_quiet_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_TOKEN", "fake-token")
    monkeypatch.delenv("ADMIN_DISCORD_IDS", raising=False)
    monkeypatch.delenv("QUIET_HOURS_START", raising=False)
    monkeypatch.delenv("QUIET_HOURS_END", raising=False)
    monkeypatch.delenv("COOKIE_ENCRYPTION_KEY", raising=False)

    config = load_config()

    assert config.admin_discord_ids == frozenset()
    assert config.quiet_hours_start == datetime.time(2, 0)
    assert config.quiet_hours_end == datetime.time(6, 0)
    assert config.cookie_encryption_key == b""
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_config.py -v
```

Expected: FAIL (`CoreConfig`에 새 필드가 없어 `TypeError`)

- [ ] **Step 4: 구현**

```python
# core/config.py
from __future__ import annotations

import datetime
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
    admin_discord_ids: frozenset[str]
    quiet_hours_start: datetime.time
    quiet_hours_end: datetime.time
    cookie_encryption_key: bytes


def _parse_time(raw: str, default: datetime.time) -> datetime.time:
    if not raw:
        return default
    hour, _, minute = raw.partition(":")
    return datetime.time(int(hour), int(minute))


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

    admin_ids_raw = os.environ.get("ADMIN_DISCORD_IDS", "")
    admin_discord_ids = frozenset(
        part.strip() for part in admin_ids_raw.split(",") if part.strip()
    )

    quiet_start = _parse_time(os.environ.get("QUIET_HOURS_START", ""), datetime.time(2, 0))
    quiet_end = _parse_time(os.environ.get("QUIET_HOURS_END", ""), datetime.time(6, 0))

    cookie_key = os.environ.get("COOKIE_ENCRYPTION_KEY", "").encode()

    return CoreConfig(
        discord_token=token,
        skills_dirs=skills_dirs,
        db_path=db_path,
        test_guild_id=test_guild_id,
        admin_discord_ids=admin_discord_ids,
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
        cookie_encryption_key=cookie_key,
    )
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_config.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 6: `.env.example` 갱신**

```bash
# .env.example (없으면 새로 만든다. 실제 값은 .env에만 채운다)
cat > .env.example <<'EOF'
DISCORD_TOKEN=
SKILLS_DIRS=skills
DB_PATH=data/yuni.db
TEST_GUILD_ID=
ADMIN_DISCORD_IDS=
QUIET_HOURS_START=02:00
QUIET_HOURS_END=06:00
# Fernet.generate_key()로 만든 urlsafe base64 32바이트 키
COOKIE_ENCRYPTION_KEY=
EOF
```

- [ ] **Step 7: 커밋**

```bash
git add pyproject.toml uv.lock core/config.py tests/core/test_config.py tests/core/test_bot_wiring.py .env.example
git commit -m "core: add admin ids, quiet hours, cookie key to config"
```

---

## Task 2: ToolContext 확장 — guild_id, 이벤트 내기, 이벤트 종류 추론

**Files:**
- Modify: `core/tool_types.py`
- Modify: `core/events.py`
- Modify: `tests/core/test_tool_types.py`
- Modify: `tests/core/test_events.py`

**Interfaces:**
- Consumes: `core.events.EventBus`
- Produces: `ToolContext.guild_id: str | None`(기본 `None`), `ToolContext.event_bus: EventBus`(기본값으로 빈 버스를 만든다), `ToolContext.emit(event_name, payload) -> None`(코루틴, `event_bus.emit_notify`에 위임). `core.events.handler_kind(fn) -> Literal["notify", "gather"]`(반환 타입 힌트로 추론). Task 6(bot.py 와이어링)이 레지스트리의 `@on` 핸들러를 이 함수로 분류해 버스에 등록하고, Task 16(account-link)이 `ctx.emit("players.linked", ...)`을 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_tool_types.py 에 추가
from core.events import EventBus, Section


async def test_tool_context_emit_uses_event_bus() -> None:
    bus = EventBus()
    received: list[object] = []

    async def handler(payload: object) -> None:
        received.append(payload)

    bus.register_notify("players.linked", owner_skill="account-link", fn=handler)
    ctx = ToolContext(player_id="1", tags=frozenset(), services={}, event_bus=bus)

    await ctx.emit("players.linked", {"player_id": "1"})
    await bus.drain()

    assert received == [{"player_id": "1"}]


def test_tool_context_defaults_guild_id_and_event_bus() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})
    assert ctx.guild_id is None
    assert ctx.event_bus is not None
```

```python
# tests/core/test_events.py 에 추가
from core.events import Section, handler_kind


def test_handler_kind_infers_gather_from_return_annotation() -> None:
    async def gather_handler(payload: object) -> Section | None:
        return None

    async def notify_handler(payload: object) -> None:
        return None

    assert handler_kind(gather_handler) == "gather"
    assert handler_kind(notify_handler) == "notify"
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_tool_types.py tests/core/test_events.py -v
```

Expected: FAIL (`ToolContext`에 `guild_id`/`event_bus`/`emit` 없음, `core.events.handler_kind` 없음)

- [ ] **Step 3: `core/tool_types.py` 구현**

```python
# core/tool_types.py 상단 import에 추가
from core.events import EventBus

# ToolContext 데이터클래스를 아래로 교체
@dataclass(slots=True, kw_only=True)
class ToolContext:
    """도구/이벤트 핸들러/예약 작업에 전달되는 실행 맥락. manifest.services에 없는 서비스는 접근 자체가 실패한다."""

    player_id: str
    tags: frozenset[str]
    services: Mapping[str, object] = field(default_factory=dict)
    guild_id: str | None = None
    event_bus: EventBus = field(default_factory=EventBus)

    def __getattr__(self, name: str) -> Any:
        services = object.__getattribute__(self, "services")
        if name in services:
            return services[name]
        raise UndeclaredServiceError(name)

    async def emit(self, event_name: str, payload: Any) -> None:
        """스킬이 알림 이벤트를 낸다. 모으기 이벤트는 각 스킬이 직접 내지 않고 모으는 쪽이 bus.emit_gather를 부른다."""
        await self.event_bus.emit_notify(event_name, payload)
```

(주의: `core/events.py`가 `core/tool_types.py`를 import하지 않으므로 순환 참조는 없다.)

- [ ] **Step 4: `core/events.py`에 `handler_kind` 추가**

```python
# core/events.py 끝에 추가
import typing


def handler_kind(fn: Callable[[Any], Awaitable[Any]]) -> Literal["notify", "gather"]:
    """@on 핸들러의 반환 타입 힌트로 notify/gather를 추론한다.

    @on 데코레이터(core/decorators.py)는 종류를 받지 않는다 — 핸들러가 Section을
    돌려주면(모으기), 아니면 아무것도 안 돌려주면(알림) 그 자체로 의도가 드러나기
    때문에, 스킬 작성자가 매번 kind="gather" 같은 걸 더 적을 필요가 없게 한다.
    """
    hints = typing.get_type_hints(fn)
    ret = hints.get("return")
    if ret is Section:
        return "gather"
    args = getattr(ret, "__args__", ())
    if Section in args:
        return "gather"
    return "notify"
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_tool_types.py tests/core/test_events.py -v
```

Expected: PASS

- [ ] **Step 6: 기존 테스트 전체 회귀 확인**

```bash
uv run pytest tests/core -v
```

Expected: PASS (2단계 테스트 전부 그대로 통과 — 새 필드가 기본값을 가져서 기존 `ToolContext(...)` 생성 코드가 안 깨진다)

- [ ] **Step 7: 커밋**

```bash
git add core/tool_types.py core/events.py tests/core/test_tool_types.py tests/core/test_events.py
git commit -m "core: add guild_id/event emission to ToolContext, infer event handler kind"
```

---

## Task 3: players 서비스 — 마이그레이션과 식별/계정 CRUD

**Files:**
- Create: `migrations/core/001_players.sql`
- Create: `services/common/__init__.py`
- Create: `services/common/players.py`
- Create: `tests/services/__init__.py`
- Create: `tests/services/test_players.py`

**Interfaces:**
- Consumes: `aiosqlite.Connection`(테스트는 `aiosqlite.connect(":memory:")` 뒤 직접 `migrations/core/001_players.sql`을 실행해 준비한다)
- Produces: `Player`(dataclass: `id, primary_discord_id, records_public, memos_public, goals_public, arcade_public, reviews_public, sync_report_public, first_seen_at`), `DiscordAccount`(`discord_id, player_id, is_primary, linked_at`), `PlayersService`(`create_player`, `link_account`, `unlink_account`, `set_primary`, `get_player`, `player_for_discord`, `accounts_for`, `set_visibility`). Task 4(태그·해석기)와 Task 16(account-link)이 이 클래스를 그대로 쓴다.

- [ ] **Step 1: 마이그레이션 작성**

```sql
-- migrations/core/001_players.sql
CREATE TABLE players (
    id TEXT PRIMARY KEY,
    primary_discord_id TEXT,
    records_public INTEGER NOT NULL DEFAULT 0,
    memos_public INTEGER NOT NULL DEFAULT 0,
    goals_public INTEGER NOT NULL DEFAULT 0,
    arcade_public INTEGER NOT NULL DEFAULT 0,
    reviews_public INTEGER NOT NULL DEFAULT 0,
    sync_report_public INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE player_discord_accounts (
    discord_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(id),
    linked_at TEXT NOT NULL
);

CREATE INDEX idx_player_discord_accounts_player_id ON player_discord_accounts(player_id);
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/services/test_players.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from services.common.players import PlayersService

_MIGRATION = Path("migrations/core/001_players.sql").read_text()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_create_player_sets_first_seen(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    assert player.id
    assert player.first_seen_at
    assert player.primary_discord_id is None
    assert player.sync_report_public is True
    assert player.records_public is False


async def test_link_account_sets_primary_on_first_link(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    await service.link_account(player.id, "111")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id == "111"
    accounts = await service.accounts_for(player.id)
    assert [a.discord_id for a in accounts] == ["111"]
    assert accounts[0].is_primary is True


async def test_link_second_account_does_not_change_primary(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")

    await service.link_account(player.id, "222")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id == "111"
    accounts = {a.discord_id: a.is_primary for a in await service.accounts_for(player.id)}
    assert accounts == {"111": True, "222": False}


async def test_player_for_discord_resolves_linked_account(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")

    found = await service.player_for_discord("111")
    assert found is not None
    assert found.id == player.id

    assert await service.player_for_discord("unknown") is None


async def test_set_primary_requires_linked_account(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")
    await service.link_account(player.id, "222")

    await service.set_primary(player.id, "222")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id == "222"

    with pytest.raises(ValueError, match="연결되지 않은"):
        await service.set_primary(player.id, "999")


async def test_unlink_account_reassigns_primary(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")
    await service.link_account(player.id, "222")

    await service.unlink_account("111")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id == "222"
    assert await service.player_for_discord("111") is None


async def test_unlink_last_account_leaves_player_with_no_primary(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")

    await service.unlink_account("111")

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.primary_discord_id is None
    assert await service.accounts_for(player.id) == []


async def test_set_visibility_updates_single_flag(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    await service.set_visibility(player.id, "records_public", True)

    refreshed = await service.get_player(player.id)
    assert refreshed is not None
    assert refreshed.records_public is True
    assert refreshed.memos_public is False
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_players.py -v
```

Expected: FAIL (`services.common.players` 없음)

- [ ] **Step 4: 구현**

```python
# services/common/players.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

_VISIBILITY_FIELDS = frozenset(
    {
        "records_public",
        "memos_public",
        "goals_public",
        "arcade_public",
        "reviews_public",
        "sync_report_public",
    }
)


@dataclass(slots=True, kw_only=True)
class Player:
    id: str
    primary_discord_id: str | None
    records_public: bool
    memos_public: bool
    goals_public: bool
    arcade_public: bool
    reviews_public: bool
    sync_report_public: bool
    first_seen_at: str


@dataclass(slots=True, kw_only=True)
class DiscordAccount:
    discord_id: str
    player_id: str
    is_primary: bool
    linked_at: str


def _row_to_player(row: aiosqlite.Row) -> Player:
    return Player(
        id=row["id"],
        primary_discord_id=row["primary_discord_id"],
        records_public=bool(row["records_public"]),
        memos_public=bool(row["memos_public"]),
        goals_public=bool(row["goals_public"]),
        arcade_public=bool(row["arcade_public"]),
        reviews_public=bool(row["reviews_public"]),
        sync_report_public=bool(row["sync_report_public"]),
        first_seen_at=row["first_seen_at"],
    )


class PlayersService:
    """플레이어, 디스코드 계정 연결, 공개 설정을 다룬다. 태그는 Task 4에서 더한다."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row

    async def create_player(self) -> Player:
        player_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO players (id, primary_discord_id, first_seen_at) VALUES (?, NULL, ?)",
            (player_id, now),
        )
        await self._conn.commit()
        player = await self.get_player(player_id)
        assert player is not None
        return player

    async def get_player(self, player_id: str) -> Player | None:
        cursor = await self._conn.execute("SELECT * FROM players WHERE id = ?", (player_id,))
        row = await cursor.fetchone()
        return _row_to_player(row) if row else None

    async def player_for_discord(self, discord_id: str) -> Player | None:
        cursor = await self._conn.execute(
            "SELECT p.* FROM players p JOIN player_discord_accounts a ON a.player_id = p.id "
            "WHERE a.discord_id = ?",
            (discord_id,),
        )
        row = await cursor.fetchone()
        return _row_to_player(row) if row else None

    async def accounts_for(self, player_id: str) -> list[DiscordAccount]:
        cursor = await self._conn.execute(
            "SELECT a.discord_id, a.player_id, a.linked_at, p.primary_discord_id "
            "FROM player_discord_accounts a JOIN players p ON p.id = a.player_id "
            "WHERE a.player_id = ? ORDER BY a.linked_at",
            (player_id,),
        )
        rows = await cursor.fetchall()
        return [
            DiscordAccount(
                discord_id=row["discord_id"],
                player_id=row["player_id"],
                is_primary=row["discord_id"] == row["primary_discord_id"],
                linked_at=row["linked_at"],
            )
            for row in rows
        ]

    async def link_account(self, player_id: str, discord_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_discord_accounts (discord_id, player_id, linked_at) VALUES (?, ?, ?)",
            (discord_id, player_id, now),
        )
        cursor = await self._conn.execute(
            "SELECT primary_discord_id FROM players WHERE id = ?", (player_id,)
        )
        row = await cursor.fetchone()
        if row is not None and row["primary_discord_id"] is None:
            await self._conn.execute(
                "UPDATE players SET primary_discord_id = ? WHERE id = ?", (discord_id, player_id)
            )
        await self._conn.commit()

    async def unlink_account(self, discord_id: str) -> None:
        cursor = await self._conn.execute(
            "SELECT player_id FROM player_discord_accounts WHERE discord_id = ?", (discord_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return
        player_id = row["player_id"]

        await self._conn.execute(
            "DELETE FROM player_discord_accounts WHERE discord_id = ?", (discord_id,)
        )

        cursor = await self._conn.execute(
            "SELECT primary_discord_id FROM players WHERE id = ?", (player_id,)
        )
        prow = await cursor.fetchone()
        if prow is not None and prow["primary_discord_id"] == discord_id:
            remaining = await self._conn.execute(
                "SELECT discord_id FROM player_discord_accounts WHERE player_id = ? "
                "ORDER BY linked_at LIMIT 1",
                (player_id,),
            )
            next_row = await remaining.fetchone()
            new_primary = next_row["discord_id"] if next_row else None
            await self._conn.execute(
                "UPDATE players SET primary_discord_id = ? WHERE id = ?", (new_primary, player_id)
            )
        await self._conn.commit()

    async def set_primary(self, player_id: str, discord_id: str) -> None:
        cursor = await self._conn.execute(
            "SELECT 1 FROM player_discord_accounts WHERE player_id = ? AND discord_id = ?",
            (player_id, discord_id),
        )
        if await cursor.fetchone() is None:
            raise ValueError(f"'{discord_id}'는 이 플레이어에 연결되지 않은 계정이다")
        await self._conn.execute(
            "UPDATE players SET primary_discord_id = ? WHERE id = ?", (discord_id, player_id)
        )
        await self._conn.commit()

    async def set_visibility(self, player_id: str, field: str, value: bool) -> None:
        if field not in _VISIBILITY_FIELDS:
            raise ValueError(f"'{field}'는 공개 설정 필드가 아니다: {sorted(_VISIBILITY_FIELDS)}")
        await self._conn.execute(
            f"UPDATE players SET {field} = ? WHERE id = ?", (1 if value else 0, player_id)
        )
        await self._conn.commit()
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_players.py -v
```

Expected: PASS (8 tests)

- [ ] **Step 6: 커밋**

```bash
git add migrations/core/001_players.sql services/common/__init__.py services/common/players.py \
  tests/services/__init__.py tests/services/test_players.py
git commit -m "services: add players identity and account linking storage"
```

---

## Task 4: players 서비스 — 태그 CRUD와 PlayerRef 해석기

**Files:**
- Create: `migrations/core/002_player_tags.sql`
- Modify: `core/permissions.py` (`STATUS_ONLY_TAGS` 추가)
- Modify: `services/common/players.py`
- Modify: `tests/services/test_players.py`

**Interfaces:**
- Consumes: `core.permissions.STATUS_ONLY_TAGS`, `core.resolvers.ResolutionError`, `core.tool_types.ToolContext`
- Produces: `PlayersService.grant_tag(player_id, tag, *, source)`, `.revoke_tag(player_id, tag)`, `.tags_for(player_id) -> set[str]`(`TagProvider` 프로토콜 구현), `.tag_source(player_id, tag) -> str | None`, `.players_with_tag(tag, source=None) -> list[str]`, `.resolve(raw, ctx) -> Player`(`Resolver` 프로토콜 구현, `PlayerRef`용). Task 6(bot.py 와이어링)이 `PlayersService`를 `tag_provider`와 `PlayerRef` 해석기 자리에 그대로 꽂는다. Task 17(admin 스킬)이 `grant_tag`/`revoke_tag`/`players_with_tag("admin")`를 쓴다.

- [ ] **Step 1: 마이그레이션 작성**

```sql
-- migrations/core/002_player_tags.sql
CREATE TABLE player_tags (
    player_id TEXT NOT NULL REFERENCES players(id),
    tag TEXT NOT NULL,
    source TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (player_id, tag)
);
```

- [ ] **Step 2: `core/permissions.py`에 상태 태그 상수 추가**

```python
# core/permissions.py 상단에 추가 (TagRequirement 위)
# "상태 태그"는 서비스가 상태를 보고 자동으로만 붙이고 뗀다. 손으로(admin의 grant_tag/
# revoke_tag 포함) 못 바꾼다. 지금은 아무 서비스도 이 태그를 실제로 붙이지 않지만,
# 이름은 2단계 레지스트리(core/registry.py의 CORE_DEFINED_TAGS)에 이미 있다.
STATUS_ONLY_TAGS: frozenset[str] = frozenset({"chunithm-linked", "chunithm-synced"})
```

- [ ] **Step 3: 실패하는 테스트 작성**

```python
# tests/services/test_players.py 에 추가
from core.resolvers import ResolutionError
from core.tool_types import ToolContext


async def test_grant_and_revoke_tag(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    await service.grant_tag(player.id, "tester", source="admin:111")
    assert await service.tags_for(player.id) == {"tester"}
    assert await service.tag_source(player.id, "tester") == "admin:111"

    await service.revoke_tag(player.id, "tester")
    assert await service.tags_for(player.id) == set()


async def test_grant_tag_rejects_status_tags(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    with pytest.raises(ValueError, match="상태 태그"):
        await service.grant_tag(player.id, "chunithm-linked", source="admin:111")


async def test_players_with_tag_filters_by_source(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    env_admin = await service.create_player()
    manual_admin = await service.create_player()
    await service.grant_tag(env_admin.id, "admin", source="env_bootstrap")
    await service.grant_tag(manual_admin.id, "admin", source="admin:111")

    assert set(await service.players_with_tag("admin")) == {env_admin.id, manual_admin.id}
    assert await service.players_with_tag("admin", source="env_bootstrap") == [env_admin.id]


async def test_resolve_player_ref_self(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})

    resolved = await service.resolve("나", ctx)
    assert resolved.id == player.id


async def test_resolve_player_ref_mention(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    target = await service.create_player()
    await service.link_account(target.id, "123456789012345678")
    caller = await service.create_player()
    ctx = ToolContext(player_id=caller.id, tags=frozenset(), services={})

    resolved = await service.resolve("<@123456789012345678>", ctx)
    assert resolved.id == target.id

    resolved_bare = await service.resolve("<@!123456789012345678>", ctx)
    assert resolved_bare.id == target.id


async def test_resolve_player_ref_unknown_mention_fails(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    caller = await service.create_player()
    ctx = ToolContext(player_id=caller.id, tags=frozenset(), services={})

    with pytest.raises(ResolutionError, match="모름"):
        await service.resolve("<@999999999999999999>", ctx)


async def test_resolve_player_ref_free_text_fails(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    caller = await service.create_player()
    ctx = ToolContext(player_id=caller.id, tags=frozenset(), services={})

    with pytest.raises(ResolutionError, match="모름"):
        await service.resolve("크로노", ctx)
```

- [ ] **Step 4: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_players.py -v
```

Expected: FAIL (`grant_tag`/`resolve` 등이 없음)

- [ ] **Step 5: 구현**

```python
# services/common/players.py 상단 import에 추가
import re

from core.permissions import STATUS_ONLY_TAGS
from core.resolvers import ResolutionError
from core.tool_types import ToolContext

_MENTION_RE = re.compile(r"^<@!?(\d{15,25})>$")
_BARE_ID_RE = re.compile(r"^\d{15,25}$")
_SELF_WORDS = frozenset({"나", "저", "me", "자신"})


# PlayersService 클래스 끝에 메서드 추가 (set_visibility 다음)

    async def grant_tag(self, player_id: str, tag: str, *, source: str) -> None:
        if tag in STATUS_ONLY_TAGS:
            raise ValueError(f"'{tag}'는 상태 태그라 손으로 못 바꾼다")
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_tags (player_id, tag, source, granted_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (player_id, tag) DO UPDATE SET source = excluded.source, "
            "granted_at = excluded.granted_at",
            (player_id, tag, source, now),
        )
        await self._conn.commit()

    async def revoke_tag(self, player_id: str, tag: str) -> None:
        if tag in STATUS_ONLY_TAGS:
            raise ValueError(f"'{tag}'는 상태 태그라 손으로 못 바꾼다")
        await self._conn.execute(
            "DELETE FROM player_tags WHERE player_id = ? AND tag = ?", (player_id, tag)
        )
        await self._conn.commit()

    async def tags_for(self, player_id: str) -> set[str]:
        """TagProvider 프로토콜 구현. core.permissions.TagProvider와 구조적으로 맞는다."""
        cursor = await self._conn.execute(
            "SELECT tag FROM player_tags WHERE player_id = ?", (player_id,)
        )
        rows = await cursor.fetchall()
        return {row["tag"] for row in rows}

    async def tag_source(self, player_id: str, tag: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT source FROM player_tags WHERE player_id = ? AND tag = ?", (player_id, tag)
        )
        row = await cursor.fetchone()
        return row["source"] if row else None

    async def players_with_tag(self, tag: str, *, source: str | None = None) -> list[str]:
        if source is None:
            cursor = await self._conn.execute(
                "SELECT player_id FROM player_tags WHERE tag = ?", (tag,)
            )
        else:
            cursor = await self._conn.execute(
                "SELECT player_id FROM player_tags WHERE tag = ? AND source = ?", (tag, source)
            )
        rows = await cursor.fetchall()
        return [row["player_id"] for row in rows]

    async def resolve(self, raw: str, ctx: ToolContext) -> Player:
        """PlayerRef 해석기. 지원 범위는 설계서 합의된 단순화 1번 참고:
        멘션(<@id>/<@!id>), 디스코드 스노우플레이크 숫자열, '나'/'저'/'me'/'자신'뿐이다."""
        text = raw.strip()

        if text in _SELF_WORDS:
            player = await self.get_player(ctx.player_id)
            if player is None:
                raise ResolutionError(message="누구인지 모름")
            return player

        mention_match = _MENTION_RE.match(text)
        if mention_match:
            discord_id = mention_match.group(1)
        elif _BARE_ID_RE.match(text):
            discord_id = text
        else:
            raise ResolutionError(message="누구인지 모름")

        player = await self.player_for_discord(discord_id)
        if player is None:
            raise ResolutionError(message="누구인지 모름")
        return player
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_players.py -v
```

Expected: PASS (14 tests)

- [ ] **Step 7: 커밋**

```bash
git add migrations/core/002_player_tags.sql core/permissions.py services/common/players.py \
  tests/services/test_players.py
git commit -m "services: add player tag storage and PlayerRef resolver"
```

---

## Task 5: 코어 — 계정 연결용 범스킬 데이터 스캔/삭제 유틸리티

**Files:**
- Create: `core/player_data.py`
- Create: `tests/core/test_player_data.py`
- Modify: `core/tool_types.py`

**Interfaces:**
- Consumes: `aiosqlite.Connection`
- Produces: `CORE_OWNED_TABLES: frozenset[str]`, `discover_skill_tables(conn) -> tuple[str, ...]`, `PlayerDataScanner(conn, table_names)`(`.count(player_id) -> dict[str, int]`, `.delete(player_id) -> dict[str, int]`), `ToolContext.player_data: PlayerDataScanner | None`(기본 `None`). Task 16(account-link)이 연결 중 "버릴 데이터" 확인에 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/core/test_player_data.py
from collections.abc import AsyncIterator

import aiosqlite
import pytest

from core.player_data import PlayerDataScanner, discover_skill_tables


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(
        """
        CREATE TABLE players (id TEXT PRIMARY KEY, primary_discord_id TEXT, first_seen_at TEXT);
        CREATE TABLE player_tags (player_id TEXT, tag TEXT, source TEXT, granted_at TEXT);
        CREATE TABLE goals_items (id TEXT PRIMARY KEY, player_id TEXT, text TEXT);
        CREATE TABLE goals_history (id TEXT PRIMARY KEY, player_id TEXT, at TEXT);
        INSERT INTO goals_items VALUES ('g1', 'p1', '목표 1');
        INSERT INTO goals_items VALUES ('g2', 'p1', '목표 2');
        INSERT INTO goals_items VALUES ('g3', 'p2', '목표 3');
        INSERT INTO goals_history VALUES ('h1', 'p1', 'now');
        """
    )
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_discover_skill_tables_excludes_core_tables(conn: aiosqlite.Connection) -> None:
    tables = await discover_skill_tables(conn)
    assert tables == ("goals_history", "goals_items")


async def test_scanner_counts_only_matching_player(conn: aiosqlite.Connection) -> None:
    tables = await discover_skill_tables(conn)
    scanner = PlayerDataScanner(conn=conn, table_names=tables)

    counts = await scanner.count("p1")
    assert counts == {"goals_items": 2, "goals_history": 1}

    counts_p2 = await scanner.count("p2")
    assert counts_p2 == {"goals_items": 1}


async def test_scanner_delete_removes_rows_and_returns_prior_counts(conn: aiosqlite.Connection) -> None:
    tables = await discover_skill_tables(conn)
    scanner = PlayerDataScanner(conn=conn, table_names=tables)

    deleted = await scanner.delete("p1")
    assert deleted == {"goals_items": 2, "goals_history": 1}

    cursor = await conn.execute("SELECT player_id FROM goals_items")
    remaining = {row[0] for row in await cursor.fetchall()}
    assert remaining == {"p2"}
    assert await scanner.count("p1") == {}
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_player_data.py -v
```

Expected: FAIL (`core.player_data` 없음)

- [ ] **Step 3: 구현**

```python
# core/player_data.py
from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

# players/settings/memory/arcades/schedule 등 코어 자신의 테이블. "모든 스킬 테이블"에서
# 뺀다 — 이들은 각자의 서비스(players.unlink_account, memory.clear 등)가 다룬다.
CORE_OWNED_TABLES: frozenset[str] = frozenset(
    {
        "schema_migrations",
        "players",
        "player_discord_accounts",
        "player_tags",
        "settings_values",
        "settings_skill_toggles",
        "player_memory",
        "arcades",
        "arcade_aliases",
        "arcade_visits",
        "schedule_recurring",
        "schedule_once",
    }
)


async def discover_skill_tables(conn: aiosqlite.Connection) -> tuple[str, ...]:
    """player_id 컬럼이 있는 테이블 중 코어 자신의 테이블을 뺀 것 — "모든 스킬 테이블"이다.

    스킬 이름 목록을 레지스트리에서 받는 대신 실제 DB 스키마를 직접 본다. 그래서 스킬이
    늘어도(또는 테스트 픽스처가 임시 테이블을 더해도) 이 함수를 고칠 필요가 없다.
    """
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    rows = await cursor.fetchall()
    tables: list[str] = []
    for row in rows:
        name = row[0]
        if name in CORE_OWNED_TABLES:
            continue
        info_cursor = await conn.execute(f'PRAGMA table_info("{name}")')
        columns = await info_cursor.fetchall()
        if any(col[1] == "player_id" for col in columns):
            tables.append(name)
    return tuple(sorted(tables))


@dataclass(slots=True, kw_only=True)
class PlayerDataScanner:
    """계정 연결에서 "버릴 데이터"를 세고 지운다. core/bot.py가 기동 시 한 번 만들어
    ToolContext에 실어 스킬(주로 account-link)에 넘긴다."""

    conn: aiosqlite.Connection
    table_names: tuple[str, ...]

    async def count(self, player_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in self.table_names:
            cursor = await self.conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE player_id = ?', (player_id,)
            )
            row = await cursor.fetchone()
            count = row[0] if row else 0
            if count:
                counts[table] = count
        return counts

    async def delete(self, player_id: str) -> dict[str, int]:
        counts = await self.count(player_id)
        for table in counts:
            await self.conn.execute(f'DELETE FROM "{table}" WHERE player_id = ?', (player_id,))
        await self.conn.commit()
        return counts
```

```python
# core/tool_types.py: import 추가
from core.player_data import PlayerDataScanner

# ToolContext에 필드 추가 (event_bus 다음)
    player_data: PlayerDataScanner | None = None
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_player_data.py tests/core/test_tool_types.py -v
```

Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add core/player_data.py core/tool_types.py tests/core/test_player_data.py
git commit -m "core: add cross-skill player data scan/delete utility for account linking"
```

---

## Task 6: 코어 와이어링 1차 — 실제 플레이어 식별, 이벤트 버스 연결, 데모 부여 장치 제거

**Files:**
- Modify: `core/executor.py`
- Modify: `core/slash.py`
- Modify: `core/bot.py`
- Modify: `tests/fixtures/skills_ok/practice/SKILL.md`
- Modify: `tests/fixtures/skills_ok/practice/skill.py`
- Modify: `tests/core/test_slash.py`
- Modify: `tests/core/test_bot_wiring.py`

**Interfaces:**
- Consumes: `services.common.players.PlayersService`(Task 3·4), `core.player_data.discover_skill_tables`/`PlayerDataScanner`(Task 5), `core.events.EventBus`/`handler_kind`(Task 2)
- Produces: `ToolExecutor.services`(속성), `ToolExecutor.event_bus`(속성), `ToolExecutor.player_data`(속성), `SlashPlanEntry.skill_services: list[str]`. 이후 모든 서비스 Task가 `ToolExecutor(services={...})`의 `services` 딕셔너리에 자기 자신을 더한다(Task 18에서 최종 조립).

이번 Task부터 2단계의 "데모 태그 부여" 임시 장치(`DEMO_GRANT_TAG_KEY`)를 걷어낸다 — `players` 서비스가 실제로 생겼으니 `practice` 픽스처도 (미래의 모든 스킬이 그럴 것처럼) `ctx.players.grant_tag`를 직접 부른다.

- [ ] **Step 1: 픽스처를 새 방식으로 바꾸고, 실패하는 테스트 작성**

```yaml
# tests/fixtures/skills_ok/practice/SKILL.md 의 services 줄만 교체
services: [players]
```

```python
# tests/fixtures/skills_ok/practice/skill.py 교체
from core.decorators import slash_only, tool
from core.tool_types import ToolContext, ToolResult


@slash_only("연습 체험시작")
async def start_demo(ctx: ToolContext) -> ToolResult:
    """연습 체험을 시작해 practice-unlocked 태그를 받는다."""
    await ctx.players.grant_tag(ctx.player_id, "practice-unlocked", source="self:practice")
    return ToolResult(data={"unlocked": "practice-unlocked"})


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

`tests/core/test_slash.py`의 `_practice_tree()`는 지금 동기 함수로 `InMemoryTagProvider`만으로 트리를 만들고, 5곳(`test_data_only_tool_reply_has_content`, `test_start_demo_grants_practice_unlocked_tag_and_unlocks_view`, `test_confirm_view_rejects_other_players`, `test_confirm_button_shows_tool_output`, `test_confirm_button_handler_passes_presser_identity_to_executor`)에서 쓰인다. 이걸 실제 `PlayersService`(비동기 DB 연결 필요)로 바꾸므로, 함수 대신 pytest 비동기 픽스처로 바꾼다. `_FakeInteraction`에도 `guild_id`를 더한다(새 `_dispatch`가 읽는다).

```python
# tests/core/test_slash.py: 상단 import에 추가
from collections.abc import AsyncIterator

import aiosqlite

from services.common.players import PlayersService

_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
```

```python
# tests/core/test_slash.py: _FakeInteraction 교체 (guild_id 추가)
class _FakeInteraction:
    """슬래시 콜백과 버튼 콜백이 실제로 쓰는 속성(user.id, guild_id, response.send_message)만 흉내 낸다."""

    def __init__(self, user_id: int, *, guild_id: int | None = None) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.response = _FakeResponse()
```

```python
# tests/core/test_slash.py: _practice_tree() 함수를 아래 픽스처로 교체
@pytest.fixture
async def practice_tree() -> AsyncIterator[tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService]]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION)
    await conn.commit()
    players = PlayersService(conn)

    registry = SkillRegistry([FIXTURES / "skills_ok"])
    registry.load()
    executor = ToolExecutor(
        resolvers=ResolverRegistry(),
        tag_provider=players,
        tag_definitions=registry.tag_definitions(),
        services={"players": players},
    )
    client = discord.Client(intents=discord.Intents.none())
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(client)
    attach_to_tree(tree, build_slash_plan(registry.tool_specs()), executor=executor)
    try:
        yield tree, executor, players
    finally:
        await conn.close()
```

이제 5개 테스트를 아래로 교체한다(함수 인자로 `practice_tree`를 받고, 본문에서 `tree, _, tag_provider = _practice_tree()` 자리를 픽스처 언패킹으로 바꾼다):

```python
# tests/core/test_slash.py: 5개 테스트 교체
async def test_data_only_tool_reply_has_content(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    tree, _, players = practice_tree
    player = await players.create_player()
    await players.link_account(player.id, "1")
    await players.grant_tag(player.id, "practice-unlocked", source="test")
    interaction = _fake(1)

    await _practice_command(tree, "보기").callback(interaction)  # pyright: ignore[reportCallIssue]

    [call] = interaction.response.calls
    assert call["content"]
    assert not call.get("ephemeral")


async def test_start_demo_grants_practice_unlocked_tag_and_unlocks_view(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    tree, _, players = practice_tree
    view_command = _practice_command(tree, "보기")

    locked = _fake(42)
    await view_command.callback(locked)  # pyright: ignore[reportCallIssue]
    assert locked.response.calls[0].get("ephemeral") is True  # 아직 잠겨 있다

    start = _fake(42)
    await _practice_command(tree, "체험시작").callback(start)  # pyright: ignore[reportCallIssue]
    assert start.response.calls[0]["content"]

    player = await players.player_for_discord("42")
    assert player is not None
    assert "practice-unlocked" in await players.tags_for(player.id)

    unlocked = _fake(42)
    await view_command.callback(unlocked)  # pyright: ignore[reportCallIssue]
    assert not unlocked.response.calls[0].get("ephemeral")

    # 다른 디스코드 계정은 참조도 안 됐으니 플레이어 자체가 아직 없다.
    assert await players.player_for_discord("7") is None


async def test_confirm_view_rejects_other_players(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    tree, _, _ = practice_tree
    view = await _issue_reset_confirm(tree, user_id=1)

    intruder = _fake(2)
    allowed = await view.interaction_check(intruder)  # pyright: ignore[reportArgumentType]

    assert allowed is False
    [call] = intruder.response.calls
    assert call.get("ephemeral") is True

    owner = _fake(1)
    assert await view.interaction_check(owner) is True  # pyright: ignore[reportArgumentType]
    assert owner.response.calls == []


async def test_confirm_button_shows_tool_output(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    tree, _, _ = practice_tree
    view = await _issue_reset_confirm(tree, user_id=1)

    owner = _fake(1)
    await view.confirm_button.callback(owner)  # pyright: ignore[reportCallIssue, reportUnknownMemberType]

    [call] = owner.response.calls
    assert "reset: True" in call["content"]


async def test_confirm_button_handler_passes_presser_identity_to_executor(
    practice_tree: tuple[discord.app_commands.CommandTree[Any], ToolExecutor, PlayersService],
) -> None:
    # interaction_check를 건너뛰어도(두 번째 방어선) 실행기가 다른 플레이어의 확인을 거부해야 한다.
    tree, _, _ = practice_tree
    view = await _issue_reset_confirm(tree, user_id=1)

    intruder = _fake(2)
    await view.confirm_button.callback(intruder)  # pyright: ignore[reportCallIssue, reportUnknownMemberType]
    [call] = intruder.response.calls
    assert call.get("ephemeral") is True
    assert "reset" not in (call["content"] or "")

    owner = _fake(1)
    await view.confirm_button.callback(owner)  # pyright: ignore[reportCallIssue, reportUnknownMemberType]
    assert "reset: True" in owner.response.calls[0]["content"]
```

(`@pytest.mark.asyncio`가 붙어 있던 함수는 떼도 된다 — `asyncio_mode = "auto"`라 `async def test_...`만으로 충분하다. 남겨 둬도 무해하다.)

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_slash.py -v
```

Expected: FAIL (`ctx.players`가 없음 — `ToolContext.services`가 비어 있다, `PlayersService` import 오류 등)

- [ ] **Step 3: `core/executor.py` 수정**

```python
# core/executor.py: import 추가
from collections.abc import Mapping

from core.events import EventBus
from core.player_data import PlayerDataScanner

# ToolExecutor.__init__ 교체
    def __init__(
        self,
        *,
        resolvers: ResolverRegistry,
        tag_provider: TagProvider,
        tag_definitions: dict[str, DefinedTag],
        services: Mapping[str, object] | None = None,
        event_bus: EventBus | None = None,
        player_data: PlayerDataScanner | None = None,
    ) -> None:
        self._resolvers = resolvers
        self._tag_provider = tag_provider
        self._tag_definitions = tag_definitions
        self._services = services or {}
        self._event_bus = event_bus or EventBus()
        self._player_data = player_data
        self._pending: dict[str, _PendingConfirm] = {}

    @property
    def tag_provider(self) -> TagProvider:
        """슬래시 콜백(core/slash.py)이 실행기와 같은 태그 제공자를 공유하기 위해 쓴다."""
        return self._tag_provider

    @property
    def services(self) -> Mapping[str, object]:
        """등록된 모든 공통/게임 서비스. 슬래시 콜백이 스킬별로 걸러 ToolContext에 싣는다."""
        return self._services

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    @property
    def player_data(self) -> PlayerDataScanner | None:
        return self._player_data
```

- [ ] **Step 4: `core/slash.py`에서 데모 장치 제거 + 실제 식별/이벤트로 교체**

```python
# core/slash.py: 삭제
# - DEMO_GRANT_TAG_KEY 상수
# - _apply_demo_grant 함수 전체
# - SlashPlanEntry.skill_defined_tags 필드
# - _send_result/_build_command/ConfirmView의 grantable_tags 매개변수와 전달부

# core/slash.py: import 추가
from services.common.players import PlayersService

# SlashPlanEntry에 필드 추가 (skill_tags_any 다음)
    skill_services: list[str] = field(default_factory=list)

# build_slash_plan의 SlashPlanEntry(...) 생성부에서 skill_defined_tags=... 줄을 지우고 추가
# (_FakeSkill처럼 manifest가 services를 안 가진 테스트 더미도 있어 getattr로 받는다 —
# 기존의 skill_tags/skill_tags_any/skill_defined_tags도 같은 이유로 getattr를 쓴다)
                skill_services=list(getattr(manifest, "services", [])),

# _send_result를 grantable_tags 없이 단순화
async def _send_result(interaction: discord.Interaction, result: ToolResult) -> None:
    await interaction.response.send_message(**render_result(result))

# ConfirmView.__init__에서 grantable_tags 매개변수/속성 제거, _finish도 grantable_tags 없이 _send_result 호출
# _build_command의 _dispatch를 교체
    async def _dispatch(interaction: discord.Interaction, options: dict[str, Any]) -> None:
        discord_id = str(interaction.user.id)
        players: PlayersService = executor.services["players"]  # type: ignore[assignment]

        player = await players.player_for_discord(discord_id)
        is_new_player = player is None
        if player is None:
            player = await players.create_player()
            await players.link_account(player.id, discord_id)

        tags = frozenset(await executor.tag_provider.tags_for(player.id))
        skill_services = {
            name: executor.services[name] for name in entry.skill_services if name in executor.services
        }
        ctx = ToolContext(
            player_id=player.id,
            tags=tags,
            services=skill_services,
            guild_id=str(interaction.guild_id) if interaction.guild_id else None,
            event_bus=executor.event_bus,
            player_data=executor.player_data,
        )

        if is_new_player:
            await executor.event_bus.emit_notify("players.first_seen", {"player_id": player.id})

        outcome = await executor.invoke(
            entry.spec, ctx, options, skill_tags=entry.skill_tags, skill_tags_any=entry.skill_tags_any
        )

        if outcome.needs_confirm:
            view = ConfirmView(executor=executor, token=outcome.confirm_token or "", requester_id=player.id)
            await interaction.response.send_message("확인해 주세요.", view=view)
            return

        result = outcome.result
        assert result is not None
        await _send_result(interaction, result)
```

(`ConfirmView._finish`가 쓰던 `player_id = str(interaction.user.id)`도 같은 방식(플레이어 식별)으로 바꿔야 확인 버튼을 다른 계정으로 눌러도 같은 플레이어로 맞는다 — `executor.confirm(token, confirmed=..., player_id=player.id)`에 넘기는 `player_id`를 위 식별 결과로 바꾼다. `requester_id`도 `ToolExecutor.invoke`가 이미 `ctx.player_id`로 저장하므로 플레이어 단위로 일관된다.)

- [ ] **Step 5: `core/bot.py` 수정 — DB 연결을 계속 열어 두고, 서비스/이벤트 버스/ADMIN 부트스트랩을 조립**

```python
# core/bot.py 교체
from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
import discord
from discord import app_commands

from core.config import CoreConfig, load_config
from core.contract_checks import run_all_contract_checks
from core.events import EventBus, handler_kind
from core.executor import ToolExecutor
from core.migrations import run_migrations
from core.player_data import PlayerDataScanner, discover_skill_tables
from core.registry import LoadedSkill, SkillRegistry
from core.resolvers import ResolverRegistry
from core.slash import attach_to_tree, build_slash_plan
from core.tool_types import PlayerRef
from services.common.players import PlayersService

logger = logging.getLogger(__name__)

CORE_MIGRATIONS_ROOT = Path("migrations/core")


class YuniClient(discord.Client):
    def __init__(self, *, intents: discord.Intents, test_guild_id: int | None, conn: aiosqlite.Connection) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.conn = conn
        self._test_guild_id = test_guild_id

    async def setup_hook(self) -> None:
        if self._test_guild_id:
            guild = discord.Object(id=self._test_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def close(self) -> None:
        await self.conn.close()
        await super().close()


def _migration_roots(skills: list[LoadedSkill]) -> list[tuple[str, Path]]:
    skill_roots = sorted((skill.manifest.name, skill.path / "migrations") for skill in skills)
    return [("core", CORE_MIGRATIONS_ROOT), *skill_roots]


async def _sync_admin_bootstrap(players: PlayersService, admin_discord_ids: frozenset[str]) -> None:
    """ADMIN_DISCORD_IDS에만 쓰인 admin 태그를 그 목록과 맞춘다.

    admin 스킬로 "추가로" 받은 admin 태그(source가 env_bootstrap이 아닌 것)는 건드리지
    않는다 — 그 사람들은 다른 경로로 admin이 됐으므로 .env 목록과 무관하게 유지된다.
    """
    for discord_id in admin_discord_ids:
        player = await players.player_for_discord(discord_id)
        if player is None:
            player = await players.create_player()
            await players.link_account(player.id, discord_id)
        await players.grant_tag(player.id, "admin", source="env_bootstrap")

    current_accounts: set[str] = set()
    for discord_id in admin_discord_ids:
        player = await players.player_for_discord(discord_id)
        if player is not None:
            for account in await players.accounts_for(player.id):
                current_accounts.add(account.discord_id)

    for player_id in await players.players_with_tag("admin", source="env_bootstrap"):
        accounts = await players.accounts_for(player_id)
        if not any(a.discord_id in admin_discord_ids for a in accounts):
            await players.revoke_tag(player_id, "admin")


def _register_event_handlers(bus: EventBus, skills: list[LoadedSkill]) -> None:
    for skill in skills:
        for handler in skill.code.event_handlers:
            kind = handler_kind(handler.fn)
            if kind == "gather":
                bus.register_gather(handler.event_name, owner_skill=skill.manifest.name, fn=handler.fn)
            else:
                bus.register_notify(handler.event_name, owner_skill=skill.manifest.name, fn=handler.fn)


async def build_client(config: CoreConfig) -> tuple[YuniClient, SkillRegistry, ToolExecutor]:
    config.db_path.parent.mkdir(parents=True, exist_ok=True)

    registry = SkillRegistry(config.skills_dirs)
    registry.load()
    for skill_name, violations in run_all_contract_checks(registry).items():
        if violations:
            registry.disable(skill_name, "계약 검사 실패: " + "; ".join(violations))
    for skill_dir, reason in registry.failures().items():
        logger.warning("skill disabled: %s (%s)", skill_dir, reason)

    roots = _migration_roots(registry.enabled_skills())
    conn = await aiosqlite.connect(config.db_path)
    await run_migrations(conn, roots=roots)

    players = PlayersService(conn)
    await _sync_admin_bootstrap(players, config.admin_discord_ids)

    skill_tables = await discover_skill_tables(conn)
    player_data = PlayerDataScanner(conn=conn, table_names=skill_tables)

    event_bus = EventBus()
    _register_event_handlers(event_bus, registry.enabled_skills())

    resolvers = ResolverRegistry()
    resolvers.register(PlayerRef, players)

    services: dict[str, object] = {"players": players}

    executor = ToolExecutor(
        resolvers=resolvers,
        tag_provider=players,
        tag_definitions=registry.tag_definitions(),
        services=services,
        event_bus=event_bus,
        player_data=player_data,
    )

    intents = discord.Intents.default()
    intents.message_content = True
    client = YuniClient(intents=intents, test_guild_id=config.test_guild_id, conn=conn)

    plan = build_slash_plan(registry.tool_specs())
    attach_to_tree(client.tree, plan, executor=executor)

    return client, registry, executor


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config()
    client, _registry, _executor = await build_client(config)
    try:
        await client.start(config.discord_token)
    finally:
        if not client.is_closed():
            await client.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
```

`tests/core/test_bot_wiring.py`가 `build_client(config)`의 반환값 개수(2개 → 3개)에 맞춰 구조분해하는 줄들을 `client, registry, _executor = await build_client(config)`처럼 고친다. 또한 테스트가 끝난 뒤 `await client.conn.close()` 또는 `await client.close()`로 연결을 닫아야 파일 기반 임시 DB(`tmp_path`)가 다음 테스트에 영향을 주지 않는다.

`_sync_admin_bootstrap`은 Review Focus 2번("env 동기화가 수동 부여를 건드리지 않는가")이 직접 가리키는 함수다 — `build_client`를 통째로 거치는 통합 테스트만으로는 "수동으로 admin을 받은 사람은 `.env` 목록이 바뀌어도 그대로 유지된다"는 조건을 놓치기 쉽다. 전용 단위 테스트를 더한다:

```python
# tests/core/test_bot_wiring.py 에 추가 (Path, aiosqlite는 이미 import돼 있을 것이다 — 없으면 더한다)
from core.bot import _sync_admin_bootstrap
from services.common.players import PlayersService


async def test_sync_admin_bootstrap_does_not_touch_manually_granted_admin() -> None:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/001_players.sql").read_text())
    await conn.executescript(Path("migrations/core/002_player_tags.sql").read_text())
    await conn.commit()
    players = PlayersService(conn)

    env_admin = await players.create_player()
    await players.link_account(env_admin.id, "111")
    manual_admin = await players.create_player()
    await players.link_account(manual_admin.id, "222")
    await players.grant_tag(manual_admin.id, "admin", source="admin:111")

    await _sync_admin_bootstrap(players, frozenset({"111"}))
    assert await players.tags_for(env_admin.id) == {"admin"}
    assert await players.tags_for(manual_admin.id) == {"admin"}  # 수동 부여는 그대로

    # .env에서 111이 빠지면 env_bootstrap 소스만 회수된다.
    await _sync_admin_bootstrap(players, frozenset())
    assert await players.tags_for(env_admin.id) == set()
    assert await players.tags_for(manual_admin.id) == {"admin"}  # 여전히 안 건드린다

    await conn.close()
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_slash.py tests/core/test_bot_wiring.py -v
```

Expected: PASS

- [ ] **Step 7: 전체 회귀 확인**

```bash
uv run pytest -v
```

Expected: PASS (2단계까지의 모든 테스트가 새 와이어링으로도 그대로 통과)

- [ ] **Step 8: 커밋**

```bash
git add core/executor.py core/slash.py core/bot.py \
  tests/fixtures/skills_ok/practice/SKILL.md tests/fixtures/skills_ok/practice/skill.py \
  tests/core/test_slash.py tests/core/test_bot_wiring.py
git commit -m "core: wire real player identity, event bus, and admin bootstrap; drop stage-2 demo grant"
```

---

## Task 7: secrets 서비스 — clal 쿠키급 비밀값 암호화/복호화

**Files:**
- Create: `services/common/secrets.py`
- Create: `tests/services/test_secrets.py`

**Interfaces:**
- Consumes: `cryptography.fernet.Fernet`
- Produces: `DecryptionError`, `SecretsService(key: bytes)`(`.encrypt(plaintext) -> bytes`, `.decrypt(ciphertext) -> str`). 4단계 `chunithm_net` 서비스가 clal 쿠키를 다룰 때 이 클래스로 암호화해 DB에 저장하고, 쓸 때만 복호화한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/test_secrets.py
import logging

from cryptography.fernet import Fernet

from services.common.secrets import DecryptionError, SecretsService


def test_round_trip() -> None:
    service = SecretsService(Fernet.generate_key())

    ciphertext = service.encrypt("clal=super-secret-cookie")

    assert ciphertext != b"super-secret-cookie"
    assert service.decrypt(ciphertext) == "clal=super-secret-cookie"


def test_wrong_key_fails_to_decrypt() -> None:
    service_a = SecretsService(Fernet.generate_key())
    service_b = SecretsService(Fernet.generate_key())

    ciphertext = service_a.encrypt("secret")

    try:
        service_b.decrypt(ciphertext)
        raise AssertionError("다른 키로 복호화가 성공하면 안 된다")
    except DecryptionError:
        pass


def test_decrypted_value_never_logged(caplog: "pytest.LogCaptureFixture") -> None:  # noqa: F821
    service = SecretsService(Fernet.generate_key())
    ciphertext = service.encrypt("clal=super-secret-cookie")

    with caplog.at_level(logging.DEBUG):
        plaintext = service.decrypt(ciphertext)

    assert plaintext == "clal=super-secret-cookie"
    assert "super-secret-cookie" not in caplog.text
```

(테스트 파일 맨 위에 `import pytest`를 더해 `caplog` 타입 힌트를 정리한다. `# noqa: F821`은 지운다.)

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_secrets.py -v
```

Expected: FAIL (`services.common.secrets` 없음)

- [ ] **Step 3: 구현**

```python
# services/common/secrets.py
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class DecryptionError(ValueError):
    pass


class SecretsService:
    """clal 쿠키 같은 비밀값의 암호화/복호화. 복호화한 평문은 반환값으로만 나가고, 이
    클래스는 그 값을 로그로도, 예외 메시지로도 남기지 않는다 — 호출하는 쪽(4단계
    chunithm_net 서비스)도 같은 규칙을 지켜야 비밀값이 스킬/로그/LLM 입력으로 새지 않는다."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode()
        except InvalidToken as exc:
            raise DecryptionError("복호화할 수 없다 — 키가 바뀌었거나 값이 손상됐다") from exc
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_secrets.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add services/common/secrets.py tests/services/test_secrets.py
git commit -m "services: add Fernet-based secrets encryption for cookies"
```

---

## Task 8: settings 서비스 — 플레이어·서버 설정값과 서버별 스킬 켜기/끄기

**Files:**
- Create: `migrations/core/003_settings.sql`
- Create: `services/common/settings.py`
- Create: `tests/services/test_settings.py`

**Interfaces:**
- Consumes: `aiosqlite.Connection`
- Produces: `SettingsService(conn)`(`.get_value`, `.set_value`, `.is_skill_enabled`, `.set_skill_enabled`). Task 9(코어 와이어링 2차·`/설정` 명령)와 Task 17(admin의 `toggle_skill`)이 이 클래스를 쓴다.

- [ ] **Step 1: 마이그레이션 작성**

```sql
-- migrations/core/003_settings.sql
CREATE TABLE settings_values (
    scope TEXT NOT NULL,       -- 'player' | 'guild'
    scope_id TEXT NOT NULL,    -- player_id 또는 guild_id(디스코드 서버 id 문자열)
    skill TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,       -- JSON으로 인코딩한 스칼라
    updated_by TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, scope_id, skill, name)
);

CREATE TABLE settings_skill_toggles (
    guild_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, skill)
);
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/services/test_settings.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from services.common.settings import SettingsService

_MIGRATION = Path("migrations/core/003_settings.sql").read_text()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_get_value_returns_default_when_missing(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    value = await service.get_value(scope="player", scope_id="p1", skill="yuni-bond", name="hint", default=True)

    assert value is True


async def test_set_then_get_value_round_trips_json_scalars(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    await service.set_value(
        scope="player", scope_id="p1", skill="yuni-bond", name="hint", value=False, updated_by="p1"
    )
    value = await service.get_value(scope="player", scope_id="p1", skill="yuni-bond", name="hint", default=True)

    assert value is False


async def test_set_value_is_scoped_per_player(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    await service.set_value(
        scope="player", scope_id="p1", skill="yuni-bond", name="hint", value=False, updated_by="p1"
    )

    other = await service.get_value(scope="player", scope_id="p2", skill="yuni-bond", name="hint", default=True)
    assert other is True


async def test_skill_enabled_defaults_to_true(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    assert await service.is_skill_enabled(guild_id="g1", skill="web-qa") is True


async def test_set_skill_enabled_is_scoped_per_guild(conn: aiosqlite.Connection) -> None:
    service = SettingsService(conn)

    await service.set_skill_enabled(guild_id="g1", skill="web-qa", enabled=False, updated_by="admin1")

    assert await service.is_skill_enabled(guild_id="g1", skill="web-qa") is False
    assert await service.is_skill_enabled(guild_id="g2", skill="web-qa") is True
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_settings.py -v
```

Expected: FAIL (`services.common.settings` 없음)

- [ ] **Step 4: 구현**

```python
# services/common/settings.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aiosqlite


class SettingsService:
    """manifest.settings에 선언된 플레이어·서버 설정값과, 서버별 스킬 켜기/끄기를 저장한다."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_value(
        self, *, scope: str, scope_id: str, skill: str, name: str, default: Any
    ) -> Any:
        cursor = await self._conn.execute(
            "SELECT value FROM settings_values WHERE scope = ? AND scope_id = ? AND skill = ? AND name = ?",
            (scope, scope_id, skill, name),
        )
        row = await cursor.fetchone()
        return default if row is None else json.loads(row[0])

    async def set_value(
        self, *, scope: str, scope_id: str, skill: str, name: str, value: Any, updated_by: str
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO settings_values (scope, scope_id, skill, name, value, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (scope, scope_id, skill, name) DO UPDATE SET "
            "value = excluded.value, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (scope, scope_id, skill, name, json.dumps(value), updated_by, now),
        )
        await self._conn.commit()

    async def is_skill_enabled(self, *, guild_id: str, skill: str) -> bool:
        cursor = await self._conn.execute(
            "SELECT enabled FROM settings_skill_toggles WHERE guild_id = ? AND skill = ?",
            (guild_id, skill),
        )
        row = await cursor.fetchone()
        return True if row is None else bool(row[0])

    async def set_skill_enabled(self, *, guild_id: str, skill: str, enabled: bool, updated_by: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO settings_skill_toggles (guild_id, skill, enabled, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (guild_id, skill) DO UPDATE SET enabled = excluded.enabled, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (guild_id, skill, 1 if enabled else 0, updated_by, now),
        )
        await self._conn.commit()
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_settings.py -v
```

Expected: PASS (5 tests)

- [ ] **Step 6: 커밋**

```bash
git add migrations/core/003_settings.sql services/common/settings.py tests/services/test_settings.py
git commit -m "services: add player/guild settings storage and per-guild skill toggle"
```

---

## Task 9: 서버별 스킬 켜기/끄기 집행 + 공통 `/설정` 슬래시 명령

**Files:**
- Modify: `core/manifest.py` (`SettingSpec.scope` 추가)
- Modify: `core/executor.py`
- Modify: `core/slash.py`
- Create: `core/settings_slash.py`
- Modify: `core/bot.py`
- Modify: `tests/core/test_manifest.py`
- Modify: `tests/core/test_executor.py`
- Create: `tests/core/test_settings_slash.py`

**Interfaces:**
- Consumes: `services.common.settings.SettingsService`(Task 8), `services.common.players.PlayersService`(Task 3·4), `core.registry.SkillRegistry`
- Produces: `SettingSpec.scope: Literal["player", "guild"]`(기본 `"player"`), `ToolExecutor(settings=...)`, `core.settings_slash.build_settings_group(registry, settings, players) -> app_commands.Group`. Task 17(admin의 `toggle_skill`)이 `SettingsService.set_skill_enabled`을 쓴다.

- [ ] **Step 1: `SettingSpec`에 `scope` 추가 — 실패하는 테스트**

```python
# tests/core/test_manifest.py: test_parse_manifest_defines_tags_and_settings의 assert 아래에 추가
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
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_manifest.py -v
```

Expected: FAIL (`SettingSpec`에 `scope` 없음)

- [ ] **Step 3: `core/manifest.py` 수정**

```python
# core/manifest.py: SettingSpec에 필드 추가 (description 다음)
class SettingSpec(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    name: str
    kind: Literal["bool", "int", "float", "str"]
    default: Any
    description: str
    scope: Literal["player", "guild"] = "player"
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_manifest.py -v
```

Expected: PASS

- [ ] **Step 5: 실패하는 실행기 테스트 — 서버별 스킬 끄기**

```python
# tests/core/test_executor.py 에 추가 (ToolExecutor, ResolverRegistry, InMemoryTagProvider는 이미 import돼 있다)
from services.common.settings import SettingsService


class _FakeSettings:
    def __init__(self) -> None:
        self._disabled: set[tuple[str, str]] = set()

    def disable(self, guild_id: str, skill: str) -> None:
        self._disabled.add((guild_id, skill))

    async def is_skill_enabled(self, *, guild_id: str, skill: str) -> bool:
        return (guild_id, skill) not in self._disabled


async def test_invoke_blocks_tool_when_skill_disabled_for_guild() -> None:
    @tool(slash="보더")
    async def lookup(ctx: ToolContext) -> ToolResult:
        """더미."""
        return ToolResult(data={})

    settings = _FakeSettings()
    settings.disable("g1", "chunithm-border")
    executor = ToolExecutor(
        resolvers=ResolverRegistry(),
        tag_provider=InMemoryTagProvider(),
        tag_definitions={},
        settings=settings,  # type: ignore[arg-type]
    )
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    outcome = await executor.invoke(
        lookup.__yuni_tool__, ctx, {}, skill_name="chunithm-border"  # pyright: ignore[reportFunctionMemberAccess]
    )

    assert outcome.result is not None
    assert outcome.result.error == "모르는 명령이에요"


async def test_invoke_allows_tool_when_no_guild_context() -> None:
    @tool(slash="보더2")
    async def lookup(ctx: ToolContext) -> ToolResult:
        """더미."""
        return ToolResult(data={"ok": True})

    settings = _FakeSettings()
    settings.disable("g1", "chunithm-border")
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={}, settings=settings  # type: ignore[arg-type]
    )
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id=None)

    outcome = await executor.invoke(
        lookup.__yuni_tool__, ctx, {}, skill_name="chunithm-border"  # pyright: ignore[reportFunctionMemberAccess]
    )

    assert outcome.result is not None
    assert outcome.result.data == {"ok": True}
```

- [ ] **Step 6: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_executor.py -v
```

Expected: FAIL (`invoke()`가 `settings`/`skill_name`을 모른다)

- [ ] **Step 7: `core/executor.py` 수정**

```python
# core/executor.py: Protocol 추가 (TagRequirement 위 어디든)
from typing import Protocol


class SkillToggleProvider(Protocol):
    async def is_skill_enabled(self, *, guild_id: str, skill: str) -> bool: ...


# ToolExecutor.__init__에 매개변수/필드 추가
        settings: SkillToggleProvider | None = None,
    ) -> None:
        ...
        self._settings = settings
        self._pending: dict[str, _PendingConfirm] = {}

# invoke 시그니처에 skill_name 추가
    async def invoke(
        self,
        spec: ToolSpec | SlashOnlySpec,
        ctx: ToolContext,
        raw_args: dict[str, Any],
        *,
        skill_tags: list[str] | None = None,
        skill_tags_any: list[str] | None = None,
        skill_name: str | None = None,
    ) -> ExecutionOutcome:
        """..."""
        if skill_name is not None and ctx.guild_id is not None and self._settings is not None:
            if not await self._settings.is_skill_enabled(guild_id=ctx.guild_id, skill=skill_name):
                return ExecutionOutcome(result=ToolResult(error="모르는 명령이에요"))

        schema = build_arg_schema(spec.fn)
        ...  # 나머지는 그대로
```

(끄기 검사를 스키마 검사/태그 검사보다 먼저 한다 — 꺼진 스킬이면 인자가 뭐든 상관없이 막아야 한다.)

- [ ] **Step 8: `core/slash.py`의 `_dispatch`가 `skill_name`을 넘기도록 수정**

```python
# core/slash.py: _dispatch 안의 executor.invoke(...) 호출에 skill_name 추가
        outcome = await executor.invoke(
            entry.spec, ctx, options,
            skill_tags=entry.skill_tags, skill_tags_any=entry.skill_tags_any,
            skill_name=entry.skill_name,
        )
```

- [ ] **Step 9: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_executor.py tests/core/test_slash.py -v
```

Expected: PASS

- [ ] **Step 10: `/설정` 명령 — 실패하는 테스트**

```python
# tests/core/test_settings_slash.py
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiosqlite
import discord
import pytest

from core.registry import SkillRegistry
from core.settings_slash import build_settings_group
from services.common.players import PlayersService
from services.common.settings import SettingsService

_CORE_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
    Path("migrations/core/003_settings.sql").read_text(),
]


class _FakeResponse:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        self.calls.append({"content": content, **kwargs})


class _FakeInteraction:
    def __init__(self, user_id: int, *, guild_id: int | None = None) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.response = _FakeResponse()


@pytest.fixture
async def settings_env():
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)
    settings = SettingsService(conn)
    registry = SkillRegistry([Path("tests/fixtures/skills_ok/with_settings")])
    registry.load()
    assert registry.failures() == {}
    group = build_settings_group(registry=registry, settings=settings, players=players)
    try:
        yield group, players, settings
    finally:
        await conn.close()


async def test_view_shows_default_before_any_change(settings_env) -> None:
    group, players, _ = settings_env
    player = await players.create_player()
    await players.link_account(player.id, "1")

    view = group.get_command("보기")
    assert isinstance(view, discord.app_commands.Command)
    interaction = _FakeInteraction(1)

    await view.callback(interaction)  # pyright: ignore[reportCallIssue]

    [call] = interaction.response.calls
    assert "with-settings" in call["content"]
    assert "True" in call["content"]  # 기본값


async def test_change_updates_player_scoped_value(settings_env) -> None:
    group, players, settings = settings_env
    player = await players.create_player()
    await players.link_account(player.id, "1")

    change = group.get_command("바꾸기")
    assert isinstance(change, discord.app_commands.Command)
    interaction = _FakeInteraction(1)

    await change.callback(  # pyright: ignore[reportCallIssue]
        interaction, skill="with-settings", setting="알림", value="false"
    )

    assert "바꿨다" in interaction.response.calls[0]["content"]
    value = await settings.get_value(
        scope="player", scope_id=player.id, skill="with-settings", name="알림", default=True
    )
    assert value is False


async def test_change_guild_scoped_requires_admin_tag(settings_env) -> None:
    group, players, _ = settings_env
    player = await players.create_player()
    await players.link_account(player.id, "1")

    change = group.get_command("바꾸기")
    assert isinstance(change, discord.app_commands.Command)
    interaction = _FakeInteraction(1, guild_id=999)

    await change.callback(  # pyright: ignore[reportCallIssue]
        interaction, skill="with-settings", setting="기준일", value="30"
    )

    assert "관리자만" in interaction.response.calls[0]["content"]

    await players.grant_tag(player.id, "admin", source="test")
    interaction2 = _FakeInteraction(1, guild_id=999)
    await change.callback(  # pyright: ignore[reportCallIssue]
        interaction2, skill="with-settings", setting="기준일", value="30"
    )
    assert "바꿨다" in interaction2.response.calls[0]["content"]
```

```yaml
# tests/fixtures/skills_ok/with_settings/SKILL.md (새 픽스처)
---
name: with-settings
description: 설정 두 개(플레이어/서버 범위)를 선언하는 픽스처.
examples:
  - 설정 테스트
category: 메모
game: common
version: 1
services: []
settings:
  - name: 알림
    kind: bool
    default: true
    description: 동기화 때 덧붙이는 말
    scope: player
  - name: 기준일
    kind: int
    default: 14
    description: 오랜만의 기준
    scope: guild
---

설정 화면 계약 테스트용 픽스처.
```

```python
# tests/fixtures/skills_ok/with_settings/skill.py (새 픽스처)
# 설정 화면 계약 테스트에는 도구가 필요 없다. 그래도 레지스트리가 skill.py의 존재를
# 요구하므로(core/registry.py의 _load_one), 빈 모듈을 둔다.
```

- [ ] **Step 11: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_settings_slash.py -v
```

Expected: FAIL (`core.settings_slash` 없음)

- [ ] **Step 12: `core/settings_slash.py` 구현**

```python
# core/settings_slash.py
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from core.manifest import SettingSpec
from core.permissions import TagRequirement, check_tags
from core.registry import SkillRegistry
from services.common.players import PlayersService
from services.common.settings import SettingsService


def _coerce(kind: str, raw: str) -> Any:
    if kind == "bool":
        lowered = raw.strip().lower()
        if lowered in {"true", "1", "on", "켜짐", "예"}:
            return True
        if lowered in {"false", "0", "off", "꺼짐", "아니오"}:
            return False
        raise ValueError(f"'{raw}'를 켜짐/꺼짐으로 읽을 수 없다")
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    return raw


def _find_setting(registry: SkillRegistry, skill_name: str, setting_name: str) -> SettingSpec:
    for skill in registry.enabled_skills():
        if skill.manifest.name != skill_name:
            continue
        for spec in skill.manifest.settings:
            if spec.name == setting_name:
                return spec
        raise ValueError(f"'{skill_name}'에는 '{setting_name}' 설정이 없다")
    raise ValueError(f"'{skill_name}' 스킬을 찾지 못했다")


def build_settings_group(
    *, registry: SkillRegistry, settings: SettingsService, players: PlayersService
) -> app_commands.Group:
    group = app_commands.Group(name="설정", description="플레이어·서버 설정을 보고 바꾼다")

    @group.command(name="보기", description="내 설정과(서버라면) 서버 설정을 본다")
    async def view(interaction: discord.Interaction) -> None:
        player = await players.player_for_discord(str(interaction.user.id))
        player_id = player.id if player else None
        guild_id = str(interaction.guild_id) if interaction.guild_id else None

        lines: list[str] = []
        for skill in registry.enabled_skills():
            for spec in skill.manifest.settings:
                if spec.scope == "player" and player_id is None:
                    continue
                if spec.scope == "guild" and guild_id is None:
                    continue
                scope_id = player_id if spec.scope == "player" else guild_id
                assert scope_id is not None
                value = await settings.get_value(
                    scope=spec.scope,
                    scope_id=scope_id,
                    skill=skill.manifest.name,
                    name=spec.name,
                    default=spec.default,
                )
                lines.append(
                    f"[{skill.manifest.name}] {spec.name} ({spec.scope}) = {value!r} — {spec.description}"
                )

        text = "\n".join(lines) if lines else "설정할 수 있는 항목이 없다."
        await interaction.response.send_message(text, ephemeral=True)

    @group.command(name="바꾸기", description="설정값을 바꾼다")
    @app_commands.describe(skill="스킬 이름", setting="설정 이름", value="새 값")
    async def change(interaction: discord.Interaction, skill: str, setting: str, value: str) -> None:
        try:
            spec = _find_setting(registry, skill, setting)
            coerced = _coerce(spec.kind, value)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        player = await players.player_for_discord(str(interaction.user.id))
        if player is None:
            await interaction.response.send_message(
                "먼저 봇과 한 번 상호작용해야 한다.", ephemeral=True
            )
            return

        if spec.scope == "player":
            scope_id = player.id
        else:
            if interaction.guild_id is None:
                await interaction.response.send_message(
                    "서버 설정은 서버 안에서만 바꿀 수 있다.", ephemeral=True
                )
                return
            tags = await players.tags_for(player.id)
            if not check_tags(TagRequirement(tags=["admin"]), tags):
                await interaction.response.send_message(
                    "서버 설정은 관리자만 바꿀 수 있다.", ephemeral=True
                )
                return
            scope_id = str(interaction.guild_id)

        await settings.set_value(
            scope=spec.scope, scope_id=scope_id, skill=skill, name=setting, value=coerced,
            updated_by=player.id,
        )
        await interaction.response.send_message(f"{skill}의 '{setting}'을 {coerced!r}로 바꿨다.", ephemeral=True)

    return group
```

- [ ] **Step 13: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_settings_slash.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 14: `core/bot.py`에 와이어링 — `/설정` 등록, 실행기에 `settings` 전달**

```python
# core/bot.py: import 추가
from core.settings_slash import build_settings_group
from services.common.settings import SettingsService

# build_client 안, players/player_data를 만든 다음, ToolExecutor 생성 전에 추가
    settings_service = SettingsService(conn)
    services["settings"] = settings_service

# ToolExecutor(...) 생성에 settings 전달
    executor = ToolExecutor(
        resolvers=resolvers,
        tag_provider=players,
        tag_definitions=registry.tag_definitions(),
        services=services,
        event_bus=event_bus,
        player_data=player_data,
        settings=settings_service,
    )

# attach_to_tree(...) 다음, return 전에 추가
    client.tree.add_command(
        build_settings_group(registry=registry, settings=settings_service, players=players)
    )
```

- [ ] **Step 15: 전체 회귀 확인**

```bash
uv run pytest -v
```

Expected: PASS

- [ ] **Step 16: 커밋**

```bash
git add core/manifest.py core/executor.py core/slash.py core/settings_slash.py core/bot.py \
  tests/core/test_manifest.py tests/core/test_executor.py tests/core/test_settings_slash.py \
  tests/fixtures/skills_ok/with_settings
git commit -m "core: enforce per-guild skill toggle and add common /설정 command"
```

---

## Task 10: memory 서비스 — 저장소와 보기·지우기 API

**Files:**
- Create: `migrations/core/004_memory.sql`
- Create: `services/common/memory.py`
- Create: `tests/services/test_memory.py`

**Interfaces:**
- Consumes: `aiosqlite.Connection`
- Produces: `MemoryService(conn)`(`.get(player_id) -> str`, `.set(player_id, content)`, `.clear(player_id)`, `.get_auto_save(player_id) -> bool`, `.set_auto_save(player_id, enabled)`). 12단계의 `memory` 스킬과 자동 요약이 이 API 위에 올라간다.

- [ ] **Step 1: 마이그레이션 작성**

```sql
-- migrations/core/004_memory.sql
CREATE TABLE player_memory (
    player_id TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    auto_save INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/services/test_memory.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from services.common.memory import MemoryService

_MIGRATION = Path("migrations/core/004_memory.sql").read_text()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_get_defaults_to_empty_string(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    assert await service.get("p1") == ""


async def test_set_then_get_round_trips(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "호칭: 디짐상. 왼손이 약하다.")
    assert await service.get("p1") == "호칭: 디짐상. 왼손이 약하다."


async def test_set_rejects_content_over_1500_chars(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    with pytest.raises(ValueError, match="1500"):
        await service.set("p1", "가" * 1501)


async def test_clear_empties_content(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "내용")
    await service.clear("p1")
    assert await service.get("p1") == ""


async def test_auto_save_defaults_to_true_and_can_be_toggled(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    assert await service.get_auto_save("p1") is True

    await service.set_auto_save("p1", False)
    assert await service.get_auto_save("p1") is False


async def test_memory_is_scoped_per_player(conn: aiosqlite.Connection) -> None:
    service = MemoryService(conn)
    await service.set("p1", "p1의 메모리")
    assert await service.get("p2") == ""
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_memory.py -v
```

Expected: FAIL (`services.common.memory` 없음)

- [ ] **Step 4: 구현**

```python
# services/common/memory.py
from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

MAX_LENGTH = 1500


class MemoryService:
    """플레이어 메모리 저장소. 자동 요약(12단계)과 memory 스킬(12단계)이 쓸 보기·지우기
    API까지만 만든다 — 요약을 만드는 로직과 프롬프트 주입은 여기 없다."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get(self, player_id: str) -> str:
        cursor = await self._conn.execute(
            "SELECT content FROM player_memory WHERE player_id = ?", (player_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else ""

    async def set(self, player_id: str, content: str) -> None:
        if len(content) > MAX_LENGTH:
            raise ValueError(f"메모리는 {MAX_LENGTH}자를 넘을 수 없다({len(content)}자)")
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_memory (player_id, content, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (player_id) DO UPDATE SET content = excluded.content, "
            "updated_at = excluded.updated_at",
            (player_id, content, now),
        )
        await self._conn.commit()

    async def clear(self, player_id: str) -> None:
        await self.set(player_id, "")

    async def get_auto_save(self, player_id: str) -> bool:
        cursor = await self._conn.execute(
            "SELECT auto_save FROM player_memory WHERE player_id = ?", (player_id,)
        )
        row = await cursor.fetchone()
        return True if row is None else bool(row[0])

    async def set_auto_save(self, player_id: str, enabled: bool) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_memory (player_id, auto_save, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (player_id) DO UPDATE SET auto_save = excluded.auto_save, "
            "updated_at = excluded.updated_at",
            (player_id, 1 if enabled else 0, now),
        )
        await self._conn.commit()
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_memory.py -v
```

Expected: PASS (6 tests)

- [ ] **Step 6: 커밋**

```bash
git add migrations/core/004_memory.sql services/common/memory.py tests/services/test_memory.py
git commit -m "services: add player memory storage with view/clear/auto-save API"
```

---

## Task 11: notify 서비스 — 채널 글, 대표 계정 멘션, DM, 대화 스레드 열기

**Files:**
- Create: `services/common/notify.py`
- Create: `tests/services/test_notify.py`

**Interfaces:**
- Consumes: `services.common.players.PlayersService`, `services.common.settings.SettingsService`, `core.threads.ThreadManager`(2단계)
- Produces: `DiscordGateway` 프로토콜(`send_channel_message`, `send_dm`), `NotifyService(gateway, players, settings, threads)`(`.post`, `.dm`, `.mention`, `.open_thread`). 미래의 모든 스킬이 알림을 보낼 때 이 서비스를 거친다(디스코드 API를 직접 안 부른다).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/test_notify.py
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from core.threads import ThreadManager, ThreadTranscript
from services.common.notify import NotifyService
from services.common.players import PlayersService
from services.common.settings import SettingsService

_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
    Path("migrations/core/003_settings.sql").read_text(),
]


class _FakeGateway:
    def __init__(self) -> None:
        self.channel_messages: list[tuple[str, str]] = []
        self.dms: list[tuple[str, str]] = []

    async def send_channel_message(self, channel_id: str, content: str) -> None:
        self.channel_messages.append((channel_id, content))

    async def send_dm(self, discord_id: str, content: str) -> None:
        self.dms.append((discord_id, content))


@pytest.fixture
async def env():
    conn = await aiosqlite.connect(":memory:")
    for migration in _MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)
    settings = SettingsService(conn)
    gateway = _FakeGateway()
    notify = NotifyService(gateway=gateway, players=players, settings=settings, threads=ThreadManager())
    try:
        yield notify, players, settings, gateway
    finally:
        await conn.close()


async def test_post_sends_to_gateway(env) -> None:
    notify, _, _, gateway = env
    await notify.post("chan-1", "동기화 완료")
    assert gateway.channel_messages == [("chan-1", "동기화 완료")]


async def test_dm_uses_primary_account(env) -> None:
    notify, players, _, gateway = env
    player = await players.create_player()
    await players.link_account(player.id, "111")

    await notify.dm(player.id, "쿠키가 만료됐어요")

    assert gateway.dms == [("111", "쿠키가 만료됐어요")]


async def test_dm_noop_when_player_has_no_account(env) -> None:
    notify, players, _, gateway = env
    player = await players.create_player()

    await notify.dm(player.id, "아무도 못 받는 DM")

    assert gateway.dms == []


async def test_mention_defaults_to_primary_account(env) -> None:
    notify, players, _, _ = env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")

    mentioned = await notify.mention(player.id, "안녕하세요")

    assert mentioned == "<@111> 안녕하세요"


async def test_mention_respects_all_accounts_setting(env) -> None:
    notify, players, settings, _ = env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")
    await settings.set_value(
        scope="player", scope_id=player.id, skill="account-link", name="알림을 받을 계정",
        value="all", updated_by=player.id,
    )

    mentioned = await notify.mention(player.id, "안녕하세요")

    assert mentioned == "<@111> <@222> 안녕하세요"


async def test_mention_respects_specific_account_setting(env) -> None:
    notify, players, settings, _ = env
    player = await players.create_player()
    await players.link_account(player.id, "111")
    await players.link_account(player.id, "222")
    await settings.set_value(
        scope="player", scope_id=player.id, skill="account-link", name="알림을 받을 계정",
        value="222", updated_by=player.id,
    )

    mentioned = await notify.mention(player.id, "안녕하세요")

    assert mentioned == "<@222> 안녕하세요"


async def test_open_thread_delegates_to_thread_manager(env) -> None:
    notify, players, _, _ = env
    player = await players.create_player()
    closed: list[ThreadTranscript] = []

    class _Transport:
        async def send_message(self, text: str) -> None:
            pass

    session = await notify.open_thread(
        skill_name="chunithm-session",
        player_id=player.id,
        transport=_Transport(),
        on_close=closed.append,
        now=datetime.now(UTC),
    )

    assert session.skill_name == "chunithm-session"
    assert session.player_id == player.id
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_notify.py -v
```

Expected: FAIL (`services.common.notify` 없음)

- [ ] **Step 3: 구현**

```python
# services/common/notify.py
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.threads import ThreadSession, ThreadTranscript, ThreadTransport
from core.threads import ThreadManager
from collections.abc import Awaitable, Callable

from services.common.players import PlayersService
from services.common.settings import SettingsService

_MENTION_SETTING_SKILL = "account-link"
_MENTION_SETTING_NAME = "알림을 받을 계정"


class DiscordGateway(Protocol):
    """core/bot.py가 실제 discord.Client로 구현을 채운다. 서비스는 디스코드 API를 직접
    모른다 — 이 프로토콜이 유일한 창구다."""

    async def send_channel_message(self, channel_id: str, content: str) -> None: ...
    async def send_dm(self, discord_id: str, content: str) -> None: ...


class NotifyService:
    def __init__(
        self,
        *,
        gateway: DiscordGateway,
        players: PlayersService,
        settings: SettingsService,
        threads: ThreadManager,
    ) -> None:
        self._gateway = gateway
        self._players = players
        self._settings = settings
        self._threads = threads

    async def post(self, channel_id: str, content: str) -> None:
        await self._gateway.send_channel_message(channel_id, content)

    async def dm(self, player_id: str, content: str) -> None:
        player = await self._players.get_player(player_id)
        if player is None or player.primary_discord_id is None:
            return
        await self._gateway.send_dm(player.primary_discord_id, content)

    async def mention(self, player_id: str, content: str) -> str:
        """알림 멘션 설정(account-link의 '알림을 받을 계정')을 반영해 멘션 문자열을 만든다."""
        target = await self._settings.get_value(
            scope="player", scope_id=player_id, skill=_MENTION_SETTING_SKILL,
            name=_MENTION_SETTING_NAME, default="primary",
        )
        player = await self._players.get_player(player_id)
        if player is None:
            raise ValueError("모르는 플레이어다")

        if target == "all":
            accounts = await self._players.accounts_for(player_id)
            mentions = " ".join(f"<@{a.discord_id}>" for a in accounts)
            return f"{mentions} {content}" if mentions else content

        discord_id = player.primary_discord_id if target in ("primary", "") else target
        return f"<@{discord_id}> {content}" if discord_id else content

    async def open_thread(
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
        return await self._threads.open(
            skill_name=skill_name, player_id=player_id, transport=transport, on_close=on_close,
            idle_timeout=idle_timeout, close_timeout=close_timeout, now=now,
        )
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_notify.py -v
```

Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add services/common/notify.py tests/services/test_notify.py
git commit -m "services: add notify (channel post, mention, DM, thread open)"
```

---

## Task 12: arcades 서비스 — 서버 공유 목록, 별칭, 이름 확인, 방문 기록, ArcadeRef 해석기

**Files:**
- Create: `migrations/core/005_arcades.sql`
- Create: `services/common/arcades.py`
- Create: `tests/services/test_arcades.py`

**Interfaces:**
- Consumes: `core.resolvers.ResolutionError`, `core.tool_types.ToolContext`
- Produces: `Arcade`, `ArcadesService(conn)`(`.create`, `.list_for_guild`, `.add_alias`, `.record_visit`, `.resolve`(`ArcadeRef`용 `Resolver` 프로토콜 구현)). 8단계 `chunithm-arcade` 스킬이 이 위에 올라간다.

- [ ] **Step 1: 마이그레이션 작성**

```sql
-- migrations/core/005_arcades.sql
CREATE TABLE arcades (
    id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (guild_id, name)
);

CREATE TABLE arcade_aliases (
    arcade_id TEXT NOT NULL REFERENCES arcades(id),
    player_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (player_id, alias)
);

CREATE TABLE arcade_visits (
    arcade_id TEXT NOT NULL REFERENCES arcades(id),
    player_id TEXT NOT NULL,
    game TEXT NOT NULL,
    visited_at TEXT NOT NULL
);
```

- [ ] **Step 2: 실패하는 테스트 작성**

```python
# tests/services/test_arcades.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.common.arcades import ArcadesService

_MIGRATION = Path("migrations/core/005_arcades.sql").read_text()


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_create_and_list_for_guild(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")

    listed = await service.list_for_guild("g1")
    assert [a.name for a in listed] == ["동인천 로지텍"]
    assert arcade.guild_id == "g1"


async def test_create_duplicate_name_in_same_guild_fails(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")

    with pytest.raises(ValueError, match="이미"):
        await service.create(guild_id="g1", name="동인천 로지텍", created_by="p2")


async def test_resolve_exact_name_match(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    resolved = await service.resolve("동인천 로지텍", ctx)
    assert resolved.id == arcade.id


async def test_resolve_uses_player_alias(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")
    await service.add_alias(arcade.id, player_id="p1", alias="동로")
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    resolved = await service.resolve("동로", ctx)
    assert resolved.id == arcade.id

    other_ctx = ToolContext(player_id="p2", tags=frozenset(), services={}, guild_id="g1")
    with pytest.raises(ResolutionError, match="없음"):
        await service.resolve("동로", other_ctx)


async def test_resolve_ambiguous_returns_candidates(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")
    await service.create(guild_id="g1", name="동인천 아이플러스", created_by="p1")
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    with pytest.raises(ResolutionError) as exc_info:
        await service.resolve("동인천", ctx)
    assert set(exc_info.value.candidates) == {"동인천 로지텍", "동인천 아이플러스"}


async def test_resolve_no_match_raises_not_found(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")

    with pytest.raises(ResolutionError, match="없음"):
        await service.resolve("없는오락실", ctx)


async def test_resolve_requires_guild_context(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id=None)

    with pytest.raises(ResolutionError, match="서버"):
        await service.resolve("동인천 로지텍", ctx)


async def test_record_and_list_visits(conn: aiosqlite.Connection) -> None:
    service = ArcadesService(conn)
    arcade = await service.create(guild_id="g1", name="동인천 로지텍", created_by="p1")

    await service.record_visit(arcade.id, player_id="p1", game="chunithm")
    visits = await service.visits_for(player_id="p1", game="chunithm")

    assert [v.arcade_id for v in visits] == [arcade.id]
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_arcades.py -v
```

Expected: FAIL (`services.common.arcades` 없음)

- [ ] **Step 4: 구현**

```python
# services/common/arcades.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from core.resolvers import ResolutionError
from core.tool_types import ToolContext


@dataclass(slots=True, kw_only=True)
class Arcade:
    id: str
    guild_id: str
    name: str
    created_by: str
    created_at: str


@dataclass(slots=True, kw_only=True)
class ArcadeVisit:
    arcade_id: str
    player_id: str
    game: str
    visited_at: str


def _row_to_arcade(row: aiosqlite.Row) -> Arcade:
    return Arcade(
        id=row["id"], guild_id=row["guild_id"], name=row["name"],
        created_by=row["created_by"], created_at=row["created_at"],
    )


class ArcadesService:
    """서버 공유 오락실 목록, 플레이어별 별칭, 이름 확인, 게임별 방문 기록."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row

    async def create(self, *, guild_id: str, name: str, created_by: str) -> Arcade:
        existing = await self._conn.execute(
            "SELECT 1 FROM arcades WHERE guild_id = ? AND name = ?", (guild_id, name)
        )
        if await existing.fetchone() is not None:
            raise ValueError(f"'{name}'은 이미 이 서버에 등록된 오락실이다")

        arcade_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO arcades (id, guild_id, name, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (arcade_id, guild_id, name, created_by, now),
        )
        await self._conn.commit()
        return Arcade(id=arcade_id, guild_id=guild_id, name=name, created_by=created_by, created_at=now)

    async def list_for_guild(self, guild_id: str) -> list[Arcade]:
        cursor = await self._conn.execute(
            "SELECT * FROM arcades WHERE guild_id = ? ORDER BY name", (guild_id,)
        )
        rows = await cursor.fetchall()
        return [_row_to_arcade(row) for row in rows]

    async def add_alias(self, arcade_id: str, *, player_id: str, alias: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO arcade_aliases (arcade_id, player_id, alias, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (player_id, alias) DO UPDATE SET arcade_id = excluded.arcade_id",
            (arcade_id, player_id, alias, now),
        )
        await self._conn.commit()

    async def record_visit(self, arcade_id: str, *, player_id: str, game: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO arcade_visits (arcade_id, player_id, game, visited_at) VALUES (?, ?, ?, ?)",
            (arcade_id, player_id, game, now),
        )
        await self._conn.commit()

    async def visits_for(self, *, player_id: str, game: str) -> list[ArcadeVisit]:
        cursor = await self._conn.execute(
            "SELECT * FROM arcade_visits WHERE player_id = ? AND game = ? ORDER BY visited_at",
            (player_id, game),
        )
        rows = await cursor.fetchall()
        return [
            ArcadeVisit(
                arcade_id=row["arcade_id"], player_id=row["player_id"], game=row["game"],
                visited_at=row["visited_at"],
            )
            for row in rows
        ]

    async def resolve(self, raw: str, ctx: ToolContext) -> Arcade:
        """ArcadeRef 해석기. 새 오락실/별칭은 여기서 만들지 않는다(버튼으로 확정한 뒤에만
        저장한다 — 그건 chunithm-arcade 스킬의 mutates 도구 몫이다)."""
        if ctx.guild_id is None:
            raise ResolutionError(message="서버 밖에서는 오락실을 찾을 수 없다")
        text = raw.strip()

        cursor = await self._conn.execute(
            "SELECT * FROM arcades WHERE guild_id = ? AND name = ?", (ctx.guild_id, text)
        )
        exact = await cursor.fetchone()
        if exact is not None:
            return _row_to_arcade(exact)

        alias_cursor = await self._conn.execute(
            "SELECT a.* FROM arcades a JOIN arcade_aliases al ON al.arcade_id = a.id "
            "WHERE a.guild_id = ? AND al.player_id = ? AND al.alias = ?",
            (ctx.guild_id, ctx.player_id, text),
        )
        alias_hit = await alias_cursor.fetchone()
        if alias_hit is not None:
            return _row_to_arcade(alias_hit)

        candidates_cursor = await self._conn.execute(
            "SELECT name FROM arcades WHERE guild_id = ? AND name LIKE ?", (ctx.guild_id, f"%{text}%")
        )
        candidates = [row["name"] for row in await candidates_cursor.fetchall()]
        if candidates:
            raise ResolutionError(candidates=candidates)

        raise ResolutionError(message="그런 오락실은 이 서버에 없음")
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_arcades.py -v
```

Expected: PASS (8 tests)

- [ ] **Step 6: 커밋**

```bash
git add migrations/core/005_arcades.sql services/common/arcades.py tests/services/test_arcades.py
git commit -m "services: add arcades list, aliases, name matching, visit records"
```

---

## Task 13: http 서비스 — 캐시(hishel), ETag, 호스트별 속도 제한(aiolimiter)

**Files:**
- Create: `services/common/http.py`
- Create: `tests/services/test_http.py`

**Interfaces:**
- Consumes: `hishel`(+`anysqlite`), `aiolimiter.AsyncLimiter`, `httpx`
- Produces: `HttpService(client, min_interval_seconds=1.0)`(`.get(url) -> httpx.Response`, `.get_json(url) -> Any`, `.aclose()`), `build_http_service(cache_db_path, min_interval_seconds=1.0) -> HttpService`. 4단계 `chunithm_net`/`songdb` 서비스가 이 위에서 CHUNITHM-NET/Chunimaru를 부른다. 실제 인터넷에 접속하는 테스트는 만들지 않는다 — `httpx.MockTransport`로 가짜 업스트림을 흉내 낸다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/test_http.py
import httpx
import pytest

from services.common.http import HttpService


@pytest.fixture
def call_counter() -> list[httpx.Request]:
    return []


def _cached_handler(calls: list[httpx.Request]) -> "httpx.AsyncBaseTransport":
    from email.utils import formatdate

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            # hishel은 Date 헤더로 신선도(max-age 대비 경과 시간)를 계산한다. 실제
            # 서버는 항상 Date를 보내지만, 가짜 전송(MockTransport)은 직접 안 채우면
            # 비워지므로 캐시가 "신선"하다고 판단하지 못해 매번 다시 호출해 버린다.
            headers={
                "Cache-Control": "max-age=3600",
                "ETag": '"v1"',
                "Date": formatdate(usegmt=True),
            },
            json={"hits": len(calls)},
        )

    return httpx.MockTransport(handler)


async def test_get_json_returns_parsed_body(call_counter: list[httpx.Request]) -> None:
    import hishel

    client = hishel.AsyncCacheClient(
        transport=_cached_handler(call_counter),
        storage=hishel.AsyncInMemoryStorage(),
        controller=hishel.Controller(cacheable_methods=["GET"], cacheable_status_codes=[200]),
    )
    service = HttpService(client=client, min_interval_seconds=0.0)

    body = await service.get_json("https://example.test/songdb")

    assert body == {"hits": 1}
    await service.aclose()


async def test_second_call_within_cache_window_does_not_hit_upstream_again(
    call_counter: list[httpx.Request],
) -> None:
    import hishel

    client = hishel.AsyncCacheClient(
        transport=_cached_handler(call_counter),
        storage=hishel.AsyncInMemoryStorage(),
        controller=hishel.Controller(cacheable_methods=["GET"], cacheable_status_codes=[200]),
    )
    service = HttpService(client=client, min_interval_seconds=0.0)

    first = await service.get_json("https://example.test/songdb")
    second = await service.get_json("https://example.test/songdb")

    assert first == second == {"hits": 1}
    assert len(call_counter) == 1  # 두 번째는 캐시에서 나왔다
    await service.aclose()


async def test_limiter_is_shared_per_host_not_per_url() -> None:
    import hishel

    client = hishel.AsyncCacheClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
        storage=hishel.AsyncInMemoryStorage(),
    )
    service = HttpService(client=client, min_interval_seconds=5.0)

    a = service._limiter_for("https://example.test/a")
    b = service._limiter_for("https://example.test/b")
    c = service._limiter_for("https://other.test/a")

    assert a is b
    assert a is not c
    await service.aclose()
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_http.py -v
```

Expected: FAIL (`services.common.http` 없음)

- [ ] **Step 3: 구현**

```python
# services/common/http.py
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import anysqlite
import hishel
import httpx
from aiolimiter import AsyncLimiter

_DEFAULT_TTL_SECONDS = 3600.0


class HttpService:
    """공개 웹 페이지/JSON을 가져온다. 캐시와 ETag 재검증은 hishel이, 호스트별 호출
    간격 제한은 aiolimiter가 한다 — 둘 다 1단계부터 벤더링된 third_party 코드가 이미
    의존하는 라이브러리라 새 의존성을 더하지 않는다."""

    def __init__(self, *, client: httpx.AsyncClient, min_interval_seconds: float = 1.0) -> None:
        self._client = client
        self._min_interval = min_interval_seconds
        self._limiters: dict[str, AsyncLimiter] = {}

    def _limiter_for(self, url: str) -> AsyncLimiter:
        host = urlparse(url).netloc
        if host not in self._limiters:
            # 간격이 0이면(테스트) 사실상 무제한으로 둔다 — AsyncLimiter는 time_period<=0을 거부한다.
            period = self._min_interval if self._min_interval > 0 else 0.001
            self._limiters[host] = AsyncLimiter(1, period)
        return self._limiters[host]

    async def get(self, url: str) -> httpx.Response:
        async with self._limiter_for(url):
            return await self._client.get(url)

    async def get_json(self, url: str) -> Any:
        response = await self.get(url)
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()


async def build_http_service(*, cache_db_path: Path, min_interval_seconds: float = 1.0) -> HttpService:
    """`cache_db_path`는 코어 DB(`data/yuni.db`)와 분리된 캐시 전용 SQLite 파일이다
    (합의된 단순화 7번). hishel이 그 파일 안의 캐시 스키마를 알아서 관리한다."""
    connection = await anysqlite.connect(str(cache_db_path))
    storage = hishel.AsyncSQLiteStorage(connection=connection, ttl=_DEFAULT_TTL_SECONDS)
    controller = hishel.Controller(
        cacheable_methods=["GET"], cacheable_status_codes=[200], allow_heuristics=False
    )
    client = hishel.AsyncCacheClient(storage=storage, controller=controller)
    return HttpService(client=client, min_interval_seconds=min_interval_seconds)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_http.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add services/common/http.py tests/services/test_http.py
git commit -m "services: add http client with hishel caching and per-host rate limiting"
```

---

## Task 14: 코어 예약 저장 — 새벽 정지 시간대 회피, 영속화, catch_up

**범위 참고:** 이 Task는 "예약을 DB에 저장하고, 재시작 뒤 그날의 catch_up을 한 번 돌린다"까지만 만든다. 평상시 운영 중 실시간으로 몇 분마다 "지금 돌 예약이 있나"를 확인하는 폴링 루프는, 실제로 `@schedule`을 쓰는 스킬이 아직 하나도 없으므로(9단계 `chunithm-goals` 기한 알림이 처음이다) 그 스킬이 생기는 단계에서 `core/bot.py`에 몇 줄 더하면 된다 — 지금 그 루프를 미리 만들면 테스트할 실제 대상이 없어 공회전하는 코드가 된다. 통과 기준 3번("재시작해도 예약이 남고, 밀린 그날 예약이 켜진 뒤 한 번 돈다")은 영속화 + 기동 시 catch_up만으로 충분히 확인된다.

**Files:**
- Create: `migrations/core/006_schedules.sql`
- Create: `core/scheduler.py`
- Create: `tests/core/test_scheduler.py`
- Modify: `core/bot.py`

**Interfaces:**
- Consumes: `croniter`, `core.decorators.ScheduleSpec`(2단계), `core.events.EventBus`
- Produces: `next_fire_time(cron, after, *, quiet_start, quiet_end) -> datetime`(순수 함수), `Scheduler(conn, *, quiet_start, quiet_end)`(`.sync_recurring`, `.startup_catch_up`, `.mark_run`, `.schedule_once`, `.startup_catch_up_once`). 9단계(`chunithm-goals` 기한 알림)가 `schedule_once`를 처음 실제로 쓴다.

- [ ] **Step 1: 마이그레이션 작성**

```sql
-- migrations/core/006_schedules.sql
CREATE TABLE schedule_recurring (
    owner_skill TEXT NOT NULL,
    fn_name TEXT NOT NULL,
    cron TEXT NOT NULL,
    catch_up INTEGER NOT NULL,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    PRIMARY KEY (owner_skill, fn_name)
);

CREATE TABLE schedule_once (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_skill TEXT NOT NULL,
    event_name TEXT NOT NULL,
    payload TEXT NOT NULL,
    fire_at TEXT NOT NULL,
    catch_up INTEGER NOT NULL,
    fired_at TEXT
);
```

- [ ] **Step 2: `next_fire_time` 순수 함수 — 실패하는 테스트**

```python
# tests/core/test_scheduler.py
import datetime
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core.scheduler import OnceSchedule, Scheduler, next_fire_time

_MIGRATION = Path("migrations/core/006_schedules.sql").read_text()

QUIET_START = datetime.time(2, 0)
QUIET_END = datetime.time(6, 0)


def test_next_fire_time_outside_quiet_hours_unaffected() -> None:
    after = datetime.datetime(2026, 10, 10, 1, 0)
    result = next_fire_time("0 9 * * *", after, quiet_start=QUIET_START, quiet_end=QUIET_END)
    assert result == datetime.datetime(2026, 10, 10, 9, 0)


def test_next_fire_time_inside_quiet_hours_shifts_to_quiet_end() -> None:
    after = datetime.datetime(2026, 10, 10, 1, 0)
    result = next_fire_time("0 3 * * *", after, quiet_start=QUIET_START, quiet_end=QUIET_END)
    assert result == datetime.datetime(2026, 10, 10, 6, 0)


def test_next_fire_time_exactly_at_quiet_end_is_unaffected() -> None:
    after = datetime.datetime(2026, 10, 10, 1, 0)
    result = next_fire_time("0 6 * * *", after, quiet_start=QUIET_START, quiet_end=QUIET_END)
    assert result == datetime.datetime(2026, 10, 10, 6, 0)
```

- [ ] **Step 3: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_scheduler.py -v
```

Expected: FAIL (`core.scheduler` 없음)

- [ ] **Step 4: `next_fire_time` 구현**

```python
# core/scheduler.py
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Any

import aiosqlite
import croniter


def next_fire_time(
    cron: str, after: datetime.datetime, *, quiet_start: datetime.time, quiet_end: datetime.time
) -> datetime.datetime:
    """cron의 다음 실행 시각을 구하고, 새벽 정지 시간대(quiet_start <= t < quiet_end) 안에
    걸리면 그 시간대가 끝나는 시각으로 민다. 정지 시간대가 자정을 걸치지 않는 경우만
    다룬다(기본값 02~06시가 그렇다) — 자정을 걸치는 설정은 미확인 사항으로 남긴다."""
    candidate = croniter.croniter(cron, after).get_next(datetime.datetime)
    candidate_time = candidate.time()
    if quiet_start <= candidate_time < quiet_end:
        candidate = candidate.replace(
            hour=quiet_end.hour, minute=quiet_end.minute, second=0, microsecond=0
        )
    return candidate
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_scheduler.py -v
```

Expected: PASS (3 tests)

- [ ] **Step 6: `Scheduler` — 실패하는 테스트**

```python
# tests/core/test_scheduler.py 에 추가
@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


async def test_sync_recurring_creates_row_with_next_run(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    now = datetime.datetime(2026, 10, 10, 1, 0)

    await scheduler.sync_recurring([("chunithm-updates", "check_updates", "0 9 * * *", True)], now=now)

    due = await scheduler.startup_catch_up(now=datetime.datetime(2026, 10, 10, 9, 0))
    assert due == []  # 9시 정각은 아직 "밀렸다"가 아니다(엄격히 과거만 캐치업 대상)


async def test_sync_recurring_is_idempotent_across_restarts(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    now = datetime.datetime(2026, 10, 10, 1, 0)

    await scheduler.sync_recurring([("chunithm-updates", "check_updates", "0 9 * * *", True)], now=now)
    # "재시작"을 흉내 낸다 — 같은 스킬 코드를 다시 동기화해도 next_run_at이 안 바뀐다.
    await scheduler.sync_recurring([("chunithm-updates", "check_updates", "0 9 * * *", True)], now=now)

    cursor = await conn.execute(
        "SELECT next_run_at FROM schedule_recurring WHERE owner_skill = ? AND fn_name = ?",
        ("chunithm-updates", "check_updates"),
    )
    rows = await cursor.fetchall()
    assert len(rows) == 1


async def test_startup_catch_up_runs_only_todays_missed_schedule(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    boot_day1 = datetime.datetime(2026, 10, 9, 1, 0)
    await scheduler.sync_recurring([("chunithm-updates", "check_updates", "0 9 * * *", True)], now=boot_day1)

    # 1일차: 9시가 지나도록 봇이 안 꺼져서 한 번도 안 돌았다고 가정 — 2일차 아침에 깨운다.
    boot_day2 = datetime.datetime(2026, 10, 10, 1, 0)
    due_day2 = await scheduler.startup_catch_up(now=boot_day2)
    assert due_day2 == []  # 1일차 9시는 이미 지난 날이라 캐치업 대상이 아니다(그날 것만)

    # 2일차 9시가 지난 뒤(정지 시간대 안이라 밀렸다고 가정) 다시 깨운다.
    later_same_day = datetime.datetime(2026, 10, 10, 9, 30)
    due_same_day = await scheduler.startup_catch_up(now=later_same_day)
    assert due_same_day == [("chunithm-updates", "check_updates")]

    await scheduler.mark_run(
        "chunithm-updates", "check_updates", cron="0 9 * * *", ran_at=later_same_day
    )
    # 한 번 더 부르면 이미 처리해 더는 밀린 게 없다.
    assert await scheduler.startup_catch_up(now=later_same_day) == []


async def test_startup_catch_up_ignores_non_catch_up_schedule(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    now = datetime.datetime(2026, 10, 10, 1, 0)
    await scheduler.sync_recurring([("web-qa", "refresh_index", "0 3 * * *", False)], now=now)

    due = await scheduler.startup_catch_up(now=datetime.datetime(2026, 10, 10, 9, 0))
    assert due == []  # catch_up=False라 밀려도 캐치업하지 않는다 — 다음 날로 넘어간다


async def test_schedule_once_catches_up_only_same_day(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    fire_at = datetime.datetime(2026, 10, 10, 20, 0)
    await scheduler.schedule_once(
        owner_skill="chunithm-goals", event_name="chunithm.goal.due",
        payload={"goal_id": "g1"}, fire_at=fire_at, catch_up=True,
    )

    same_day_wakeup = datetime.datetime(2026, 10, 10, 23, 0)
    due = await scheduler.startup_catch_up_once(now=same_day_wakeup)
    assert due == [OnceSchedule(owner_skill="chunithm-goals", event_name="chunithm.goal.due", payload={"goal_id": "g1"})]


async def test_schedule_once_gives_up_after_the_day_passes(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    fire_at = datetime.datetime(2026, 10, 10, 20, 0)
    await scheduler.schedule_once(
        owner_skill="chunithm-goals", event_name="chunithm.goal.due",
        payload={"goal_id": "g1"}, fire_at=fire_at, catch_up=True,
    )

    next_day_wakeup = datetime.datetime(2026, 10, 11, 7, 0)
    due = await scheduler.startup_catch_up_once(now=next_day_wakeup)
    assert due == []


async def test_schedule_once_ignores_non_catch_up_even_same_day(conn: aiosqlite.Connection) -> None:
    scheduler = Scheduler(conn, quiet_start=QUIET_START, quiet_end=QUIET_END)
    fire_at = datetime.datetime(2026, 10, 10, 20, 0)
    await scheduler.schedule_once(
        owner_skill="chunithm-goals", event_name="chunithm.goal.due",
        payload={"goal_id": "g1"}, fire_at=fire_at, catch_up=False,
    )

    same_day_wakeup = datetime.datetime(2026, 10, 10, 23, 0)
    due = await scheduler.startup_catch_up_once(now=same_day_wakeup)
    assert due == []  # catch_up=False라 같은 날이어도 돌지 않는다 — 그냥 닫힌다
```

- [ ] **Step 7: 테스트 실패 확인**

```bash
uv run pytest tests/core/test_scheduler.py -v
```

Expected: FAIL (`Scheduler`/`OnceSchedule` 없음)

- [ ] **Step 8: `Scheduler` 구현**

```python
# core/scheduler.py 끝에 추가

@dataclass(slots=True, kw_only=True, frozen=True)
class OnceSchedule:
    owner_skill: str
    event_name: str
    payload: dict[str, Any]


class Scheduler:
    """@schedule 데코레이터가 붙은 함수 자체는 매 기동마다 레지스트리가 다시 찾아낸다.
    여기 DB에는 "다음/마지막 실행 시각" 같은 상태만 (owner_skill, fn_name) 키로 둔다."""

    def __init__(self, conn: aiosqlite.Connection, *, quiet_start: datetime.time, quiet_end: datetime.time) -> None:
        self._conn = conn
        self._quiet_start = quiet_start
        self._quiet_end = quiet_end

    async def sync_recurring(
        self, specs: list[tuple[str, str, str, bool]], *, now: datetime.datetime
    ) -> None:
        for owner_skill, fn_name, cron, catch_up in specs:
            cursor = await self._conn.execute(
                "SELECT cron FROM schedule_recurring WHERE owner_skill = ? AND fn_name = ?",
                (owner_skill, fn_name),
            )
            row = await cursor.fetchone()
            if row is not None and row[0] == cron:
                continue
            next_run = next_fire_time(cron, now, quiet_start=self._quiet_start, quiet_end=self._quiet_end)
            await self._conn.execute(
                "INSERT INTO schedule_recurring (owner_skill, fn_name, cron, catch_up, next_run_at, last_run_at) "
                "VALUES (?, ?, ?, ?, ?, NULL) "
                "ON CONFLICT (owner_skill, fn_name) DO UPDATE SET "
                "cron = excluded.cron, catch_up = excluded.catch_up, next_run_at = excluded.next_run_at",
                (owner_skill, fn_name, cron, int(catch_up), next_run.isoformat()),
            )
        await self._conn.commit()

    async def startup_catch_up(self, *, now: datetime.datetime) -> list[tuple[str, str]]:
        """기동 시 한 번 부른다. catch_up이 켜졌고 그 밀린 몫이 '오늘' 날짜인 것만 돌려준다.
        그 밖의 밀린 몫(catch_up 꺼짐, 또는 여러 날 전)은 돌리지 않고 다음 시각으로 민다.

        '밀렸다'는 엄격히 과거(next_run_at < now)만 본다 — 정확히 지금 이 순간인 것은 아직
        "missed"가 아니라 정상 운영 중 실시간 루프(지금은 없고, 9단계에서 실제 사용 스킬이
        생길 때 더한다)가 그 순간에 맞춰 돌릴 몫으로 본다."""
        cursor = await self._conn.execute(
            "SELECT owner_skill, fn_name, cron, catch_up, next_run_at FROM schedule_recurring "
            "WHERE next_run_at < ?",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        today = now.date().isoformat()
        to_run: list[tuple[str, str]] = []
        for owner_skill, fn_name, cron, catch_up, next_run_at in rows:
            if catch_up and next_run_at[:10] == today:
                to_run.append((owner_skill, fn_name))
            else:
                rescheduled = next_fire_time(cron, now, quiet_start=self._quiet_start, quiet_end=self._quiet_end)
                await self._conn.execute(
                    "UPDATE schedule_recurring SET next_run_at = ? WHERE owner_skill = ? AND fn_name = ?",
                    (rescheduled.isoformat(), owner_skill, fn_name),
                )
        await self._conn.commit()
        return to_run

    async def mark_run(self, owner_skill: str, fn_name: str, *, cron: str, ran_at: datetime.datetime) -> None:
        next_run = next_fire_time(cron, ran_at, quiet_start=self._quiet_start, quiet_end=self._quiet_end)
        await self._conn.execute(
            "UPDATE schedule_recurring SET last_run_at = ?, next_run_at = ? "
            "WHERE owner_skill = ? AND fn_name = ?",
            (ran_at.isoformat(), next_run.isoformat(), owner_skill, fn_name),
        )
        await self._conn.commit()

    async def schedule_once(
        self, *, owner_skill: str, event_name: str, payload: dict[str, Any],
        fire_at: datetime.datetime, catch_up: bool,
    ) -> int:
        cursor = await self._conn.execute(
            "INSERT INTO schedule_once (owner_skill, event_name, payload, fire_at, catch_up, fired_at) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (owner_skill, event_name, json.dumps(payload), fire_at.isoformat(), int(catch_up)),
        )
        await self._conn.commit()
        row_id = cursor.lastrowid
        assert row_id is not None
        return row_id

    async def startup_catch_up_once(self, *, now: datetime.datetime) -> list[OnceSchedule]:
        """한 번 도는 예약 중 아직 안 돈 것을 본다. catch_up이 켜졌고 '오늘' 날짜로
        예정됐던 것만 낸다 — recurring 쪽(startup_catch_up)과 완전히 같은 규칙이다.
        catch_up이 꺼졌거나 오늘이 아니면 그냥 닫는다(다시는 안 돈다 — one-off라 미룰
        '다음 시각'이 없다). '밀렸다'의 기준은 startup_catch_up과 같이 엄격히 과거다."""
        cursor = await self._conn.execute(
            "SELECT id, owner_skill, event_name, payload, fire_at, catch_up FROM schedule_once "
            "WHERE fired_at IS NULL AND fire_at < ?",
            (now.isoformat(),),
        )
        rows = await cursor.fetchall()
        today = now.date().isoformat()
        due: list[OnceSchedule] = []
        for row_id, owner_skill, event_name, payload, fire_at, catch_up in rows:
            if catch_up and fire_at[:10] == today:
                due.append(
                    OnceSchedule(owner_skill=owner_skill, event_name=event_name, payload=json.loads(payload))
                )
            await self._conn.execute(
                "UPDATE schedule_once SET fired_at = ? WHERE id = ?", (now.isoformat(), row_id)
            )
        await self._conn.commit()
        return due
```

- [ ] **Step 9: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_scheduler.py -v
```

Expected: PASS (10 tests)

- [ ] **Step 10: `core/bot.py` 와이어링 — 기동 시 동기화와 catch_up**

```python
# core/bot.py: import 추가
import datetime

from core.scheduler import Scheduler

# build_client 안, event_bus를 만든 다음에 추가
    scheduler = Scheduler(conn, quiet_start=config.quiet_hours_start, quiet_end=config.quiet_hours_end)
    now = datetime.datetime.now()
    recurring_specs = [
        (skill.manifest.name, spec.fn.__name__, spec.cron, spec.catch_up)
        for skill in registry.enabled_skills()
        for spec in skill.code.schedules
    ]
    await scheduler.sync_recurring(recurring_specs, now=now)
    due_recurring = await scheduler.startup_catch_up(now=now)
    schedule_fns = {
        (skill.manifest.name, spec.fn.__name__): spec
        for skill in registry.enabled_skills()
        for spec in skill.code.schedules
    }
    for owner_skill, fn_name in due_recurring:
        spec = schedule_fns[(owner_skill, fn_name)]
        ctx = ToolContext(
            player_id="system", tags=frozenset(), services=services, event_bus=event_bus, player_data=player_data
        )
        await spec.fn(ctx)
        await scheduler.mark_run(owner_skill, fn_name, cron=spec.cron, ran_at=now)

    due_once = await scheduler.startup_catch_up_once(now=now)
    for once in due_once:
        await event_bus.emit_notify(once.event_name, once.payload)
```

(`services` 딕셔너리는 이 시점에 `players`/`settings`가 이미 들어 있다 — Task 18에서 나머지 서비스를 더 채운다. `player_id="system"`은 예약 작업이 특정 플레이어의 요청이 아니라 코드가 시작한 일이라는 뜻이다.)

- [ ] **Step 11: 전체 회귀 확인**

```bash
uv run pytest -v
```

Expected: PASS

- [ ] **Step 12: 커밋**

```bash
git add migrations/core/006_schedules.sql core/scheduler.py tests/core/test_scheduler.py core/bot.py
git commit -m "core: persist recurring/one-off schedules with quiet-hour avoidance and same-day catch-up"
```

---

## Task 15: yuni 서비스 뼈대 — 정해진 문구와 대화 요약 인터페이스

**Files:**
- Create: `services/common/yuni.py`
- Create: `tests/services/test_yuni.py`

**Interfaces:**
- Consumes: `core.events.Section`, `core.threads.ThreadTranscript`
- Produces: `YuniService`(`.line(sections: list[Section]) -> str`, `.summarize_conversation(transcript) -> str`). 11단계가 `.line`의 내부만 LLM 호출로 바꾼다 — 인자/반환 형식이 설계서 이벤트 절의 조각 칸에 그대로 맞춰져 있어 부르는 쪽 코드는 안 바뀐다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/test_yuni.py
from core.events import Section
from core.threads import ThreadTranscript
from services.common.yuni import YuniService


def test_line_picks_highest_stage_regardless_of_order() -> None:
    service = YuniService()
    sections = [Section(stage="good"), Section(stage="great"), Section(stage="rest")]
    assert service.line(sections) == service.line([Section(stage="great")])


def test_line_close_and_rest_differ() -> None:
    service = YuniService()
    assert service.line([Section(stage="close")]) != service.line([Section(stage="rest")])


def test_line_default_when_no_section_has_a_stage() -> None:
    service = YuniService()
    assert service.line([Section(data={"x": 1})]) == service.line([])


async def test_summarize_conversation_is_a_placeholder() -> None:
    service = YuniService()
    transcript = ThreadTranscript(thread_id="t1", player_id="p1", messages=[], closed_reason="explicit")

    summary = await service.summarize_conversation(transcript)

    assert summary == ""
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/services/test_yuni.py -v
```

Expected: FAIL (`services.common.yuni` 없음)

- [ ] **Step 3: 구현**

```python
# services/common/yuni.py
from __future__ import annotations

from core.events import Section
from core.threads import ThreadTranscript

# 작을수록 높은 단계. "가장 높은 stage를 쓴다"(00-core.md)는 이 순서에서 제일 앞에 있는 것.
_STAGE_PRIORITY: list[str] = ["great", "good", "close", "rest"]

_STAGE_LINES: dict[str, str] = {
    "great": "완전 짱이에요! 최고예요!",
    "good": "좋아요, 더 가 볼 수 있어요!",
    "close": "조금만 더예요. 같이 세팅 봐 볼까요?",
    "rest": "오늘은 이 정도로도 충분해요. 쉬어도 돼요.",
}
_DEFAULT_LINE = "확인했어요."


class YuniService:
    """유니의 한마디(조각들의 data·hint·stage를 받아 정해진 문구를 돌려줌)와 대화 요약
    인터페이스. 11단계에서 실제 LLM 구현으로 바꿔 끼울 때 이 두 메서드를 부르는 쪽
    코드가 바뀌지 않도록, 인자와 반환 형식을 설계서 이벤트 절의 조각 칸에 맞춘다."""

    def line(self, sections: list[Section]) -> str:
        stages = [s.stage for s in sections if s.stage is not None]
        if not stages:
            return _DEFAULT_LINE
        highest = min(stages, key=_STAGE_PRIORITY.index)
        return _STAGE_LINES[highest]

    async def summarize_conversation(self, transcript: ThreadTranscript) -> str:
        """대화 전문을 요약한다. 11~12단계에서 싼 모델 호출로 바뀐다 — 지금은 자리만 만든다."""
        return ""
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_yuni.py -v
```

Expected: PASS (4 tests)

- [ ] **Step 5: 커밋**

```bash
git add services/common/yuni.py tests/services/test_yuni.py
git commit -m "services: add yuni skeleton returning fixed phrases by stage"
```

---

## Task 16: account-link 스킬

이 Task 전에 두 가지를 먼저 더한다: ① `ToolContext.db`(스킬이 자기 테이블에 직접 SQL을 쓰는 통로 — 다른 스킬 서비스처럼 `manifest.services`로 선언하는 게 아니라 `ctx.event_bus`/`ctx.player_data`처럼 코어가 항상 주는 능력이다. 00-core.md의 "models.py: 이 스킬 테이블의 행 타입과 SQL 쿼리"가 실제로 쓸 연결이다), ② `NotifyService.dm`에 `view` 인자(DM에 확인 버튼을 함께 보낸다 — 연결 절차의 "양쪽에 확인 버튼을 받는다"에 필요하다).

**Files:**
- Modify: `core/tool_types.py` (`db` 필드)
- Modify: `core/slash.py` (`_dispatch`가 `db=conn` 전달)
- Modify: `core/bot.py` (`YuniClient`가 아는 `conn`을 `_dispatch`가 쓰도록 — 이미 `executor`에는 없으므로 클로저로 넘긴다)
- Modify: `services/common/notify.py` (`dm(..., view=None)`)
- Modify: `services/common/players.py` (`dissolve_player`)
- Modify: `tests/services/test_notify.py`
- Create: `skills/account-link/SKILL.md`
- Create: `skills/account-link/migrations/001_init.sql`
- Create: `skills/account-link/models.py`
- Create: `skills/account-link/skill.py`
- Create: `skills/account-link/tests/test_account_link.py`

**Interfaces:**
- Consumes: `services.common.players.PlayersService`, `services.common.notify.NotifyService`, `core.player_data.PlayerDataScanner`, `core.slash.render_result`(재사용)
- Produces: `/연결 시작`, `/연결 참여`, `/연결 목록`, `/연결 대표`, `/연결 해제`(전부 `@slash_only`). `players.linked` 이벤트를 낸다.

- [ ] **Step 1: `ToolContext.db` 추가 — 실패하는 테스트**

```python
# tests/core/test_tool_types.py 에 추가
def test_tool_context_db_defaults_to_none() -> None:
    ctx = ToolContext(player_id="1", tags=frozenset(), services={})
    assert ctx.db is None
```

```bash
uv run pytest tests/core/test_tool_types.py -v   # Expected: FAIL
```

```python
# core/tool_types.py: import 추가
import aiosqlite

# ToolContext에 필드 추가 (player_data 다음)
    db: aiosqlite.Connection | None = None
```

```bash
uv run pytest tests/core/test_tool_types.py -v   # Expected: PASS
```

- [ ] **Step 2: `core/slash.py`의 `_dispatch`가 `db`를 전달하도록 수정**

`_build_command`는 지금 `executor`만 클로저로 받는다. `conn`은 `ToolExecutor`가 들고 있지 않으므로, `attach_to_tree`에 `db: aiosqlite.Connection`를 새 매개변수로 더해 `_build_command`까지 전달한다.

```python
# core/slash.py: import 추가
import aiosqlite

# attach_to_tree 시그니처에 db 추가
def attach_to_tree(
    tree: app_commands.CommandTree[Any], plan: SlashPlan, *, executor: ToolExecutor, db: aiosqlite.Connection
) -> None:
    ...
    for skill_name, entries in by_skill.items():
        try:
            _attach_skill(tree, groups, entries, executor=executor, db=db)
        ...

# _attach_skill 시그니처에 db 추가하고 _build_command(entry, executor=executor, db=db)로 전달
def _attach_skill(
    tree: app_commands.CommandTree[Any],
    groups: dict[tuple[str, ...], app_commands.Group],
    entries: list[SlashPlanEntry],
    *,
    executor: ToolExecutor,
    db: aiosqlite.Connection,
) -> None:
    built: list[tuple[tuple[str, ...], app_commands.Command[Any, Any, Any]]] = []
    for entry in entries:
        problems = slash_path_problems(" ".join(entry.path))
        if problems:
            raise SlashPlanError("; ".join(problems))
        built.append((entry.path, _build_command(entry, executor=executor, db=db)))
    ...  # 나머지는 그대로

# _build_command 시그니처와 ctx 생성부 수정
def _build_command(
    entry: SlashPlanEntry, *, executor: ToolExecutor, db: aiosqlite.Connection
) -> app_commands.Command[Any, Any, Any]:
    ...
    async def _dispatch(interaction: discord.Interaction, options: dict[str, Any]) -> None:
        ...
        ctx = ToolContext(
            player_id=player.id,
            tags=tags,
            services=skill_services,
            guild_id=str(interaction.guild_id) if interaction.guild_id else None,
            event_bus=executor.event_bus,
            player_data=executor.player_data,
            db=db,
        )
        ...  # 나머지는 그대로
```

`core/bot.py`의 `attach_to_tree(client.tree, plan, executor=executor)` 호출 두 군데(최초 와이어링, Task 9에서 추가한 것은 없음 — 한 곳뿐이다)에 `db=conn`을 더한다.

```bash
uv run pytest tests/core -v   # Expected: PASS (회귀 없음)
```

- [ ] **Step 3: `NotifyService.dm`에 `view` 추가 — 실패하는 테스트**

```python
# tests/services/test_notify.py 에 추가 (_FakeGateway를 먼저 아래로 교체)
class _FakeGateway:
    def __init__(self) -> None:
        self.channel_messages: list[tuple[str, str]] = []
        self.dms: list[tuple[str, str, object]] = []

    async def send_channel_message(self, channel_id: str, content: str) -> None:
        self.channel_messages.append((channel_id, content))

    async def send_dm(self, discord_id: str, content: str, *, view: object | None = None) -> None:
        self.dms.append((discord_id, content, view))


async def test_dm_passes_view_through(env) -> None:
    notify, players, _, gateway = env
    player = await players.create_player()
    await players.link_account(player.id, "111")

    sentinel_view = object()
    await notify.dm(player.id, "확인해 주세요", view=sentinel_view)

    assert gateway.dms == [("111", "확인해 주세요", sentinel_view)]
```

(기존 `test_dm_uses_primary_account`/`test_dm_noop_when_player_has_no_account`의 `gateway.dms == [("111", "...")]` 비교를 `[("111", "...", None)]`로 고친다.)

```bash
uv run pytest tests/services/test_notify.py -v   # Expected: FAIL
```

```python
# services/common/notify.py: dm 시그니처 교체
    async def dm(self, player_id: str, content: str, *, view: Any | None = None) -> None:
        player = await self._players.get_player(player_id)
        if player is None or player.primary_discord_id is None:
            return
        await self._gateway.send_dm(player.primary_discord_id, content, view=view)

# DiscordGateway 프로토콜도 맞춘다
class DiscordGateway(Protocol):
    async def send_channel_message(self, channel_id: str, content: str) -> None: ...
    async def send_dm(self, discord_id: str, content: str, *, view: Any | None = None) -> None: ...
```

(`Any`를 이미 import했는지 확인한다.)

```bash
uv run pytest tests/services/test_notify.py -v   # Expected: PASS
```

- [ ] **Step 4: `PlayersService.dissolve_player` 추가 — 실패하는 테스트**

```python
# tests/services/test_players.py 에 추가
async def test_dissolve_player_removes_accounts_and_tags(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.link_account(player.id, "111")
    await service.grant_tag(player.id, "tester", source="admin:1")

    await service.dissolve_player(player.id)

    assert await service.player_for_discord("111") is None
    assert await service.tags_for(player.id) == set()
```

```bash
uv run pytest tests/services/test_players.py -v   # Expected: FAIL
```

```python
# services/common/players.py: PlayersService 끝에 메서드 추가
    async def dissolve_player(self, player_id: str) -> None:
        """플레이어를 완전히 해체한다 — 계정 연결과 태그를 전부 지우고 플레이어 행은
        고아로 남긴다. 계정 연결의 '버리고 연결'에서만 쓰는 내부 정리 경로라, grant_tag/
        revoke_tag와 달리 상태 태그 보호를 거치지 않는다(admin의 손태그 변경 금지와는
        다른 종류의 작업이다)."""
        for account in await self.accounts_for(player_id):
            await self.unlink_account(account.discord_id)
        await self._conn.execute("DELETE FROM player_tags WHERE player_id = ?", (player_id,))
        await self._conn.commit()
```

```bash
uv run pytest tests/services/test_players.py -v   # Expected: PASS
```

- [ ] **Step 5: account-link 마이그레이션과 SKILL.md**

```sql
-- skills/account-link/migrations/001_init.sql
CREATE TABLE account_link_codes (
    code TEXT PRIMARY KEY,
    issuer_player_id TEXT NOT NULL,
    issuer_discord_id TEXT NOT NULL,
    joiner_discord_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    issuer_confirmed INTEGER NOT NULL DEFAULT 0,
    joiner_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE account_link_attempts (
    discord_id TEXT PRIMARY KEY,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);
```

```yaml
# skills/account-link/SKILL.md
---
name: account-link
description: 디스코드 부계정을 한 플레이어로 연결하는 방법을 안내한다. 연결과 해제는 슬래시 명령으로만 한다.
examples:
  - 디코 부계정도 연결하고 싶어
  - 연결된 계정 보여 줘
category: 계정 연결
game: common
version: 1
services: [players, notify]
emits: [players.linked]
---

## SKILL.md 본문

- 연결 방법을 물으면 "`/연결 시작`을 대표로 쓸 계정에서, `/연결 참여`를 연결할 계정에서" 순서를 그대로 안내한다.
- "저 계정이 내 부계정이야, 연결해 줘"처럼 말로 요청해도 직접 연결하지 않는다. 양쪽 계정에서 명령을 써야 한다고 말한다.
```

(이 스킬에는 `@tool`이 없다 — 전부 `@slash_only`다.)

- [ ] **Step 6: `models.py`**

```python
# skills/account-link/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite


@dataclass(slots=True, kw_only=True)
class LinkCode:
    code: str
    issuer_player_id: str
    issuer_discord_id: str
    joiner_discord_id: str | None
    status: str
    issuer_confirmed: bool
    joiner_confirmed: bool
    expires_at: str


def _row_to_code(row: aiosqlite.Row) -> LinkCode:
    return LinkCode(
        code=row["code"],
        issuer_player_id=row["issuer_player_id"],
        issuer_discord_id=row["issuer_discord_id"],
        joiner_discord_id=row["joiner_discord_id"],
        status=row["status"],
        issuer_confirmed=bool(row["issuer_confirmed"]),
        joiner_confirmed=bool(row["joiner_confirmed"]),
        expires_at=row["expires_at"],
    )


async def create_code(
    conn: aiosqlite.Connection, *, code: str, issuer_player_id: str, issuer_discord_id: str,
    now: datetime, ttl: timedelta,
) -> None:
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "INSERT INTO account_link_codes "
        "(code, issuer_player_id, issuer_discord_id, status, created_at, expires_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?)",
        (code, issuer_player_id, issuer_discord_id, now.isoformat(), (now + ttl).isoformat()),
    )
    await conn.commit()


async def get_code(conn: aiosqlite.Connection, code: str) -> LinkCode | None:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute("SELECT * FROM account_link_codes WHERE code = ?", (code,))
    row = await cursor.fetchone()
    return _row_to_code(row) if row else None


async def mark_joined(conn: aiosqlite.Connection, code: str, *, joiner_discord_id: str) -> None:
    await conn.execute(
        "UPDATE account_link_codes SET joiner_discord_id = ?, status = 'joined' WHERE code = ?",
        (joiner_discord_id, code),
    )
    await conn.commit()


async def set_confirmed(conn: aiosqlite.Connection, code: str, *, side: str) -> None:
    column = "issuer_confirmed" if side == "issuer" else "joiner_confirmed"
    await conn.execute(f"UPDATE account_link_codes SET {column} = 1 WHERE code = ?", (code,))  # noqa: S608
    await conn.commit()


async def mark_status(conn: aiosqlite.Connection, code: str, *, status: str) -> None:
    await conn.execute("UPDATE account_link_codes SET status = ? WHERE code = ?", (status, code))
    await conn.commit()


async def wrong_attempt_count(conn: aiosqlite.Connection, discord_id: str, *, now: datetime) -> int:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        "SELECT wrong_count, locked_until FROM account_link_attempts WHERE discord_id = ?", (discord_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return 0
    if row["locked_until"] and row["locked_until"] > now.isoformat():
        return 999  # 잠겨 있는 동안은 횟수와 무관하게 막힌 것으로 본다
    return row["wrong_count"]


async def record_wrong_attempt(
    conn: aiosqlite.Connection, discord_id: str, *, now: datetime, lock_after: int, cooldown: timedelta
) -> int:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        "SELECT wrong_count FROM account_link_attempts WHERE discord_id = ?", (discord_id,)
    )
    row = await cursor.fetchone()
    count = (row["wrong_count"] if row else 0) + 1
    locked_until = (now + cooldown).isoformat() if count >= lock_after else None
    await conn.execute(
        "INSERT INTO account_link_attempts (discord_id, wrong_count, locked_until) VALUES (?, ?, ?) "
        "ON CONFLICT (discord_id) DO UPDATE SET wrong_count = excluded.wrong_count, "
        "locked_until = excluded.locked_until",
        (discord_id, count, locked_until),
    )
    await conn.commit()
    return count


async def reset_wrong_attempts(conn: aiosqlite.Connection, discord_id: str) -> None:
    await conn.execute(
        "INSERT INTO account_link_attempts (discord_id, wrong_count, locked_until) VALUES (?, 0, NULL) "
        "ON CONFLICT (discord_id) DO UPDATE SET wrong_count = 0, locked_until = NULL",
        (discord_id,),
    )
    await conn.commit()
```

- [ ] **Step 7: `skill.py`**

```python
# skills/account-link/skill.py
from __future__ import annotations

import importlib.util
import secrets
import string
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import discord

from core.decorators import slash_only
from core.slash import render_result
from core.tool_types import Display, ToolContext, ToolResult

# 스킬 폴더 이름("account-link")에 하이픈이 있어 보통의 패키지 import 경로로 쓸 수 없다
# (skills/_template/tests/test_template_example.py가 skill.py를 불러오는 것과 같은 이유).
# 그래서 같은 폴더의 models.py를 파일 경로로 직접 불러온다.
_MODELS_PATH = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("_account_link_models", _MODELS_PATH)
assert _spec is not None and _spec.loader is not None
models = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(models)

CODE_LENGTH = 6
CODE_TTL = timedelta(minutes=10)
MAX_WRONG_ATTEMPTS = 5
WRONG_ATTEMPT_COOLDOWN = timedelta(minutes=10)
# 00-core.md/account-link.md: "플레이어당 최대 계정 수"의 범위는 "전역"이다. 3단계
# settings 서비스의 범위는 플레이어/서버뿐이라(core/settings_slash.py 참고) "서버와
# 무관하게 봇 전체에 하나"인 값을 표현하지 못한다. 설계서에 없는 세부 판단으로, 이
# 값은 설정이 아니라 코드 상수로 둔다.
MAX_LINKED_ACCOUNTS = 3


def _generate_code() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(CODE_LENGTH))


async def _solo_discord_id(ctx: ToolContext) -> str | None:
    accounts = await ctx.players.accounts_for(ctx.player_id)
    return accounts[0].discord_id if accounts else None


@slash_only("연결 시작")
async def start_link(ctx: ToolContext) -> ToolResult:
    """일회용 연결 코드를 발급한다."""
    accounts = await ctx.players.accounts_for(ctx.player_id)
    if len(accounts) >= MAX_LINKED_ACCOUNTS:
        return ToolResult(error=f"플레이어당 계정은 최대 {MAX_LINKED_ACCOUNTS}개까지 연결할 수 있다")

    issuer_discord_id = accounts[0].discord_id if accounts else None
    if issuer_discord_id is None:
        return ToolResult(error="계정 식별에 실패했다. 다시 시도해 주세요")

    code = _generate_code()
    assert ctx.db is not None
    await models.create_code(
        ctx.db, code=code, issuer_player_id=ctx.player_id, issuer_discord_id=issuer_discord_id,
        now=datetime.now(UTC), ttl=CODE_TTL,
    )
    return ToolResult(
        data={"code": code},
        display=Display(
            content=f"연결 코드: `{code}` (10분간 유효). 연결할 계정에서 `/연결 참여`에 입력해 주세요."
        ),
    )


async def _proceed_to_mutual_confirm(ctx: ToolContext, link_code: Any, joiner_discord_id: str) -> ToolResult:
    assert ctx.db is not None
    await models.mark_joined(ctx.db, link_code.code, joiner_discord_id=joiner_discord_id)

    issuer_view = _ConfirmButtonsView(code=link_code.code, side="issuer", ctx=ctx)
    await ctx.notify.dm(
        link_code.issuer_player_id,
        f"<@{joiner_discord_id}>이 연결을 요청했다. 같은 사람인가요?",
        view=issuer_view,
    )

    joiner_view = _ConfirmButtonsView(code=link_code.code, side="joiner", ctx=ctx)
    return ToolResult(
        data={"status": "pending_confirmation"},
        display=Display(content=f"<@{link_code.issuer_discord_id}>과 연결해요. 맞나요?", view=joiner_view),
    )


@slash_only("연결 참여")
async def join_link(ctx: ToolContext, code: str) -> ToolResult:
    """코드를 넣어 연결을 요청한다."""
    assert ctx.db is not None
    joiner_discord_id = await _solo_discord_id(ctx)
    if joiner_discord_id is None:
        return ToolResult(error="계정 식별에 실패했다. 다시 시도해 주세요")

    now = datetime.now(UTC)
    if await models.wrong_attempt_count(ctx.db, joiner_discord_id, now=now) >= MAX_WRONG_ATTEMPTS:
        return ToolResult(error="너무 많이 틀렸다. 잠시 후 다시 시도해 주세요")

    link_code = await models.get_code(ctx.db, code)
    if link_code is None or link_code.expires_at <= now.isoformat():
        await models.record_wrong_attempt(
            ctx.db, joiner_discord_id, now=now, lock_after=MAX_WRONG_ATTEMPTS, cooldown=WRONG_ATTEMPT_COOLDOWN
        )
        return ToolResult(error="코드가 틀렸거나 만료됐다")

    if link_code.status != "pending":
        # 이미 다른 사람이 참여해 'joined' 이상으로 넘어간 코드다 — 코드가 유출돼 두 번째
        # 참여자가 끼어들어도 먼저 참여한 쪽의 진행을 덮어쓰지 않는다(중복 소비 방지).
        return ToolResult(error="이미 다른 사람이 참여한 코드다")

    if link_code.issuer_player_id == ctx.player_id:
        return ToolResult(error="이미 같은 플레이어다")

    await models.reset_wrong_attempts(ctx.db, joiner_discord_id)

    counts = await ctx.player_data.count(ctx.player_id) if ctx.player_data is not None else {}
    if counts:
        view = _DiscardConfirmView(code=code, joiner_discord_id=joiner_discord_id, ctx=ctx)
        lines = "\n".join(f"- {table}: {n}건" for table, n in counts.items())
        return ToolResult(
            data={"counts": counts},
            display=Display(content=f"이 계정에 이미 데이터가 있다:\n{lines}\n버리고 연결할까요?", view=view),
        )

    return await _proceed_to_mutual_confirm(ctx, link_code, joiner_discord_id)


class _DiscardConfirmView(discord.ui.View):
    """참여자 계정에 이미 데이터가 있을 때만 뜬다. 취소하면 아무것도 바뀌지 않는다."""

    def __init__(self, *, code: str, joiner_discord_id: str, ctx: ToolContext) -> None:
        super().__init__(timeout=300)
        self._code = code
        self._joiner_discord_id = joiner_discord_id
        self._ctx = ctx

    @discord.ui.button(label="버리고 연결", style=discord.ButtonStyle.danger)
    async def discard_and_continue(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        assert self._ctx.db is not None
        link_code = await models.get_code(self._ctx.db, self._code)
        if link_code is None or link_code.expires_at <= datetime.now(UTC).isoformat():
            await interaction.response.send_message("코드가 만료됐다", ephemeral=True)
            return

        if self._ctx.player_data is not None:
            await self._ctx.player_data.delete(self._ctx.player_id)
        await self._ctx.players.dissolve_player(self._ctx.player_id)

        result = await _proceed_to_mutual_confirm(self._ctx, link_code, self._joiner_discord_id)
        await interaction.response.send_message(**render_result(result))

    @discord.ui.button(label="취소", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        await interaction.response.send_message("취소했다. 아무것도 바뀌지 않았다.", ephemeral=True)


class _ConfirmButtonsView(discord.ui.View):
    """발급자/참여자 양쪽에 각각 뜬다. 둘 다 눌러야 연결이 끝난다."""

    def __init__(self, *, code: str, side: str, ctx: ToolContext) -> None:
        super().__init__(timeout=int(CODE_TTL.total_seconds()))
        self._code = code
        self._side = side
        self._ctx = ctx

    @discord.ui.button(label="확인", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button[Any]) -> None:
        assert self._ctx.db is not None
        link_code = await models.get_code(self._ctx.db, self._code)
        if link_code is None or link_code.expires_at <= datetime.now(UTC).isoformat():
            await interaction.response.send_message("만료된 요청이다", ephemeral=True)
            return

        expected = link_code.issuer_discord_id if self._side == "issuer" else link_code.joiner_discord_id
        if str(interaction.user.id) != expected:
            await interaction.response.send_message("이건 당신의 확인 요청이 아니다", ephemeral=True)
            return

        await models.set_confirmed(self._ctx.db, self._code, side=self._side)
        refreshed = await models.get_code(self._ctx.db, self._code)
        assert refreshed is not None

        if refreshed.issuer_confirmed and refreshed.joiner_confirmed:
            assert refreshed.joiner_discord_id is not None
            await self._ctx.players.link_account(refreshed.issuer_player_id, refreshed.joiner_discord_id)
            await models.mark_status(self._ctx.db, self._code, status="completed")
            await self._ctx.emit(
                "players.linked",
                {
                    "player_id": refreshed.issuer_player_id,
                    "discord_id": refreshed.joiner_discord_id,
                    "action": "linked",
                },
            )
            await self._ctx.notify.dm(refreshed.issuer_player_id, "연결이 완료됐다.")
            await interaction.response.send_message("연결이 완료됐다!", ephemeral=True)
        else:
            await models.mark_status(self._ctx.db, self._code, status=f"{self._side}_confirmed")
            await interaction.response.send_message("확인했다. 상대의 확인을 기다리는 중이다.", ephemeral=True)


@slash_only("연결 목록")
async def list_links(ctx: ToolContext) -> ToolResult:
    """연결된 계정, 대표 여부, 연결일을 본인에게만 보여준다."""
    accounts = await ctx.players.accounts_for(ctx.player_id)
    lines = [f"<@{a.discord_id}>{' (대표)' if a.is_primary else ''} — {a.linked_at}" for a in accounts]
    return ToolResult(
        data={"accounts": [a.discord_id for a in accounts]},
        display=Display(content="\n".join(lines) if lines else "연결된 계정이 없다."),
    )


@slash_only("연결 대표")
async def set_primary(ctx: ToolContext, discord_id: str) -> ToolResult:
    """대표 계정을 바꾼다."""
    try:
        await ctx.players.set_primary(ctx.player_id, discord_id)
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(data={"primary": discord_id})


@slash_only("연결 해제")
async def unlink(ctx: ToolContext, discord_id: str) -> ToolResult:
    """연결을 푼다. 대표 계정은 대표를 넘긴 뒤에만 뗄 수 있다."""
    player = await ctx.players.get_player(ctx.player_id)
    if player is None:
        return ToolResult(error="모르는 플레이어다")
    if player.primary_discord_id == discord_id:
        accounts = await ctx.players.accounts_for(ctx.player_id)
        if len(accounts) > 1:
            return ToolResult(error="대표 계정은 먼저 다른 계정으로 대표를 넘긴 뒤에 뗄 수 있다")

    await ctx.players.unlink_account(discord_id)
    await ctx.emit("players.linked", {"player_id": ctx.player_id, "discord_id": discord_id, "action": "unlinked"})
    return ToolResult(data={"unlinked": discord_id})
```

- [ ] **Step 8: 실패하는 통합 테스트 작성**

```python
# skills/account-link/tests/test_account_link.py
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import aiosqlite
import pytest

from core.events import EventBus
from core.player_data import PlayerDataScanner, discover_skill_tables
from core.tool_types import ToolContext
from services.common.players import PlayersService

_CORE_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
]
_SKILL_MIGRATION = Path(__file__).parent.parent / "migrations" / "001_init.sql"
_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_account_link_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_message(self, content: str | None = None, **kwargs: Any) -> None:
        self.calls.append({"content": content, **kwargs})


class _FakeInteraction:
    def __init__(self, discord_id: str) -> None:
        self.user = SimpleNamespace(id=int(discord_id))
        self.response = _FakeResponse()


class _FakeNotify:
    def __init__(self) -> None:
        self.dms: list[tuple[str, str, object]] = []

    async def dm(self, player_id: str, content: str, *, view: object | None = None) -> None:
        self.dms.append((player_id, content, view))


@pytest.fixture
async def link_env():
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.executescript(_SKILL_MIGRATION.read_text())
    await conn.commit()

    players = PlayersService(conn)
    notify = _FakeNotify()
    player_data = PlayerDataScanner(conn=conn, table_names=await discover_skill_tables(conn))
    skill = _load_skill()

    def make_ctx(player_id: str) -> ToolContext:
        return ToolContext(
            player_id=player_id, tags=frozenset(), services={"players": players, "notify": notify},
            db=conn, event_bus=EventBus(), player_data=player_data,
        )

    try:
        yield skill, players, notify, make_ctx
    finally:
        await conn.close()


async def test_full_link_flow_resolves_to_same_player(link_env) -> None:
    skill, players, _, make_ctx = link_env

    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id)

    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner_ctx = make_ctx(joiner.id)

    joined = await skill.join_link(joiner_ctx, code=code)
    joiner_view = joined.display.view
    issuer_view = issuer_ctx.services["notify"].dms[-1][2]

    await issuer_view.confirm.callback(_FakeInteraction("111"))  # pyright: ignore[reportCallIssue]
    await joiner_view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    assert await players.player_for_discord("111") == await players.player_for_discord("222")
    assert (await players.player_for_discord("222")).id == issuer.id  # pyright: ignore[reportOptionalMemberAccess]


async def test_expired_code_is_rejected(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id)

    assert issuer_ctx.db is not None
    await skill.models.create_code(
        issuer_ctx.db, code="000000", issuer_player_id=issuer.id, issuer_discord_id="111",
        now=datetime.now(UTC) - timedelta(minutes=20), ttl=timedelta(minutes=10),
    )

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    result = await skill.join_link(make_ctx(joiner.id), code="000000")

    assert result.error is not None
    assert "만료" in result.error or "틀렸" in result.error
    assert await players.player_for_discord("111") != await players.player_for_discord("222")


async def test_five_wrong_attempts_locks_out_further_tries(link_env) -> None:
    skill, players, _, make_ctx = link_env
    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    ctx = make_ctx(joiner.id)

    for _ in range(skill.MAX_WRONG_ATTEMPTS):
        result = await skill.join_link(ctx, code="wrong-code")
        assert result.error is not None

    locked = await skill.join_link(ctx, code="wrong-code")
    assert locked.error is not None
    assert "너무 많이" in locked.error


async def test_one_sided_confirmation_does_not_link(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id))
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id), code=code)

    # 참여자만 확인하고, 발급자는 확인하지 않는다.
    await joined.display.view.confirm.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    assert await players.player_for_discord("111") != await players.player_for_discord("222")


async def test_cancel_discard_changes_nothing(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id))
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joiner_ctx = make_ctx(joiner.id)
    await joiner_ctx.players.grant_tag(joiner.id, "tester", source="test")

    result = await skill.join_link(joiner_ctx, code=code)
    assert result.display is not None and result.display.view is not None  # 데이터가 있어 확인 버튼이 떴다

    await result.display.view.cancel.callback(_FakeInteraction("222"))  # pyright: ignore[reportCallIssue]

    assert await players.tags_for(joiner.id) == {"tester"}  # 그대로다
    assert await players.player_for_discord("222") == joiner  # 아무것도 안 바뀌었다


async def test_second_join_with_same_code_is_rejected(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    started = await skill.start_link(make_ctx(issuer.id))
    code = started.data["code"]

    first_joiner = await players.create_player()
    await players.link_account(first_joiner.id, "222")
    await skill.join_link(make_ctx(first_joiner.id), code=code)

    second_joiner = await players.create_player()
    await players.link_account(second_joiner.id, "333")
    result = await skill.join_link(make_ctx(second_joiner.id), code=code)

    assert result.error is not None
    assert "다른 사람" in result.error
    # 첫 참여자의 진행이 덮어써지지 않았는지, models로 직접 확인한다.
    link_code = await skill.models.get_code(make_ctx(issuer.id).db, code)
    assert link_code is not None
    assert link_code.joiner_discord_id == "222"


async def test_confirm_button_rejects_expired_code(link_env) -> None:
    skill, players, _, make_ctx = link_env
    issuer = await players.create_player()
    await players.link_account(issuer.id, "111")
    issuer_ctx = make_ctx(issuer.id)
    started = await skill.start_link(issuer_ctx)
    code = started.data["code"]

    joiner = await players.create_player()
    await players.link_account(joiner.id, "222")
    joined = await skill.join_link(make_ctx(joiner.id), code=code)
    joiner_view = joined.display.view

    # 코드를 과거로 되돌려 '만료된 뒤에 버튼을 누른' 상황을 흉내 낸다.
    assert issuer_ctx.db is not None
    await issuer_ctx.db.execute(
        "UPDATE account_link_codes SET expires_at = ? WHERE code = ?",
        ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), code),
    )
    await issuer_ctx.db.commit()

    expired_click = _FakeInteraction("222")
    await joiner_view.confirm.callback(expired_click)  # pyright: ignore[reportCallIssue]

    assert "만료" in expired_click.response.calls[0]["content"]
    assert await players.player_for_discord("111") != await players.player_for_discord("222")
```

(`test_cancel_discard_changes_nothing`이 "데이터가 있는 계정"을 흉내 내려면 `player_data.count(joiner.id)`가 0이 아니어야 한다 — 태그는 `discover_skill_tables`가 보는 "스킬 테이블"이 아니므로(코어 자신의 테이블이라 `CORE_OWNED_TABLES`에서 빠진다) 이 테스트는 실제로는 `counts`가 비어 확인 버튼이 안 뜨는 경로를 탄다. 구현 때 이 픽스처에 `player_id` 컬럼을 가진 가짜 스킬 테이블을 하나 만들어 데이터를 넣어야 분기를 제대로 타게 된다 — 아래처럼 `link_env` 픽스처에 추가한다.)

```python
# link_env 픽스처의 conn.commit() 전에 추가
    await conn.executescript(
        "CREATE TABLE goals_items (id TEXT PRIMARY KEY, player_id TEXT, text TEXT);"
    )
```

```python
# test_cancel_discard_changes_nothing에서 grant_tag 대신(또는 더해서) 이렇게 데이터를 만든다
    await joiner_ctx.db.execute(
        "INSERT INTO goals_items (id, player_id, text) VALUES ('g1', ?, '목표')", (joiner.id,)
    )
    await joiner_ctx.db.commit()
```

- [ ] **Step 9: 테스트 실패 확인**

```bash
uv run pytest skills/account-link/tests/test_account_link.py -v
```

Expected: FAIL (`skills/account-link/skill.py` 등이 없음)

- [ ] **Step 10: 테스트 통과 확인**

```bash
uv run pytest skills/account-link/tests/test_account_link.py -v
```

Expected: PASS (7 tests)

- [ ] **Step 11: 전체 회귀 확인**

```bash
uv run pytest -v
```

Expected: PASS

- [ ] **Step 12: 커밋**

```bash
git add core/tool_types.py core/slash.py core/bot.py services/common/notify.py services/common/players.py \
  tests/core/test_tool_types.py tests/services/test_notify.py tests/services/test_players.py \
  skills/account-link
git commit -m "skills: add account-link with code-based mutual confirmation and data-discard guard"
```

---

## Task 17: admin 스킬

`skills` 도구는 등록된 스킬 목록과 등록 실패 이유를 보여줘야 하는데, 스킬은 `core.registry.SkillRegistry`를 직접 import하지 않는다(다른 스킬의 내부처럼 코어 내부도 서비스가 아니면 못 본다). 그래서 `ToolContext`에 읽기 전용 스냅샷 하나(`registry_snapshot`)를 코어가 기동 시 한 번 만들어 실어 준다 — `event_bus`/`player_data`/`db`와 같은 성격의 "코어가 주는 능력"이다.

**Files:**
- Create: `core/registry_snapshot.py`
- Modify: `core/tool_types.py` (`registry_snapshot` 필드)
- Modify: `core/slash.py` (`_dispatch`에 전달)
- Modify: `core/bot.py` (스냅샷 생성 + 전달)
- Create: `skills/admin/SKILL.md`
- Create: `skills/admin/skill.py`
- Create: `skills/admin/tests/test_admin.py`

**Interfaces:**
- Consumes: `services.common.players.PlayersService`, `services.common.settings.SettingsService`, `core.permissions.STATUS_ONLY_TAGS`
- Produces: `RegistrySnapshot`, `SkillSummary`, `ToolContext.registry_snapshot`. `/관리 사용량`, `/관리 스킬`, `/관리 스킬설정`, `/관리 태그부여`, `/관리 태그회수`, `/관리 상한`.

- [ ] **Step 1: `RegistrySnapshot` — 실패하는 테스트**

```python
# tests/core/test_registry_snapshot.py
from pathlib import Path

from core.registry import SkillRegistry
from core.registry_snapshot import build_snapshot

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_build_snapshot_lists_enabled_and_failed_skills() -> None:
    registry = SkillRegistry([FIXTURES / "skills_ok" / "practice", FIXTURES / "skills_bad" / "bad_category"])
    registry.load()

    snapshot = build_snapshot(registry)

    assert any(s.name == "practice" and s.enabled for s in snapshot.skills)
    assert snapshot.failures  # bad_category는 실패 이유가 있다
```

```bash
uv run pytest tests/core/test_registry_snapshot.py -v   # Expected: FAIL
```

```python
# core/registry_snapshot.py
from __future__ import annotations

from dataclasses import dataclass

from core.registry import SkillRegistry


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


def build_snapshot(registry: SkillRegistry) -> RegistrySnapshot:
    """레지스트리의 읽기 전용 스냅샷을 한 번 만든다. 스킬은 레지스트리를 직접 못 보므로
    admin의 `skills` 도구가 이 스냅샷으로만 등록 현황을 본다."""
    skills = tuple(
        SkillSummary(name=s.manifest.name, game=s.manifest.game, version=s.manifest.version, enabled=True)
        for s in registry.enabled_skills()
    )
    return RegistrySnapshot(skills=skills, failures=registry.failures())
```

```bash
uv run pytest tests/core/test_registry_snapshot.py -v   # Expected: PASS
```

- [ ] **Step 2: `ToolContext`/`_dispatch`/`bot.py`에 스냅샷 연결**

```python
# core/tool_types.py: import 추가, 필드 추가 (db 다음)
from core.registry_snapshot import RegistrySnapshot
...
    registry_snapshot: RegistrySnapshot | None = None
```

```python
# core/slash.py: _dispatch의 ToolContext(...) 생성에 registry_snapshot 추가
        ctx = ToolContext(
            ...,
            db=db,
            registry_snapshot=registry_snapshot,
        )
```

(`attach_to_tree`/`_attach_skill`/`_build_command`에 `registry_snapshot: RegistrySnapshot`도 `db`와 같은 자리에 매개변수로 더해 클로저로 넘긴다 — Task 16에서 `db`를 더한 것과 똑같은 모양이다.)

```python
# core/bot.py: import 추가
from core.registry_snapshot import build_snapshot

# attach_to_tree(...) 호출에 registry_snapshot 추가
    snapshot = build_snapshot(registry)
    attach_to_tree(client.tree, plan, executor=executor, db=conn, registry_snapshot=snapshot)
```

```bash
uv run pytest tests/core -v   # Expected: PASS (회귀 없음)
```

- [ ] **Step 3: SKILL.md**

```yaml
# skills/admin/SKILL.md
---
name: admin
description: 봇 관리. LLM 사용량과 비용, 서버별 스킬 켜기·끄기, 부여 태그, 스킬 등록 오류를 다룬다.
examples:
  - 이번 달 LLM 비용 얼마야?
  - 이 서버에서 web-qa 꺼 줘
category: 관리
game: common
version: 1
services: [players, settings]
tags: [admin]
settings:
  - name: 월 비용 상한
    kind: float
    default: 10.0
    description: 이 서버의 월 LLM 비용 상한(달러 추정)
    scope: guild
  - name: 플레이어 쿨다운
    kind: int
    default: 10
    description: 플레이어별 도구 호출 쿨다운(초)
    scope: guild
---

## SKILL.md 본문

- 바꾸는 요청은 무엇이 바뀌는지 한 줄로 설명하고 확인 버튼을 낸다.
- 비용은 추정치임을 밝힌다.
```

- [ ] **Step 4: 실패하는 테스트 작성**

```python
# skills/admin/tests/test_admin.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from core.permissions import STATUS_ONLY_TAGS
from core.tool_types import PlayerRef, ToolContext
from services.common.players import PlayersService
from services.common.settings import SettingsService

_CORE_MIGRATIONS = [
    Path("migrations/core/001_players.sql").read_text(),
    Path("migrations/core/002_player_tags.sql").read_text(),
    Path("migrations/core/003_settings.sql").read_text(),
]
_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_admin_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def admin_env():
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)
    settings = SettingsService(conn)
    skill = _load_skill()

    def make_ctx(player_id: str, *, guild_id: str | None = "g1") -> ToolContext:
        return ToolContext(
            player_id=player_id, tags=frozenset(), services={"players": players, "settings": settings},
            guild_id=guild_id,
        )

    try:
        yield skill, players, settings, make_ctx
    finally:
        await conn.close()


async def test_grant_tag_rejects_status_tags(admin_env) -> None:
    skill, players, _, make_ctx = admin_env
    admin = await players.create_player()
    target = await players.create_player()
    await players.link_account(target.id, "222")

    result = await skill.grant_tag(make_ctx(admin.id), target=target, tag="chunithm-linked")

    assert result.error is not None
    assert "상태 태그" in result.error


async def test_grant_and_revoke_skill_tag(admin_env) -> None:
    skill, players, _, make_ctx = admin_env
    admin = await players.create_player()
    target = await players.create_player()

    granted = await skill.grant_tag(make_ctx(admin.id), target=target, tag="tester")
    assert granted.error is None
    assert await players.tags_for(target.id) == {"tester"}

    revoked = await skill.revoke_tag(make_ctx(admin.id), target=target, tag="tester")
    assert revoked.error is None
    assert await players.tags_for(target.id) == set()


async def test_revoke_tag_blocks_removing_the_last_admin(admin_env) -> None:
    skill, players, _, make_ctx = admin_env
    sole_admin = await players.create_player()
    await players.grant_tag(sole_admin.id, "admin", source="env_bootstrap")

    result = await skill.revoke_tag(make_ctx(sole_admin.id), target=sole_admin, tag="admin")

    assert result.error is not None
    assert "마지막" in result.error
    assert await players.tags_for(sole_admin.id) == {"admin"}


async def test_revoke_tag_allows_removing_admin_when_another_remains(admin_env) -> None:
    skill, players, _, make_ctx = admin_env
    admin1 = await players.create_player()
    admin2 = await players.create_player()
    await players.grant_tag(admin1.id, "admin", source="env_bootstrap")
    await players.grant_tag(admin2.id, "admin", source="admin:1")

    result = await skill.revoke_tag(make_ctx(admin1.id), target=admin2, tag="admin")

    assert result.error is None
    assert await players.tags_for(admin2.id) == set()


async def test_toggle_skill_sets_guild_scoped_flag(admin_env) -> None:
    skill, _, settings, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id="g1")

    result = await skill.toggle_skill(ctx, skill_name="web-qa", enabled=False)

    assert result.error is None
    assert await settings.is_skill_enabled(guild_id="g1", skill="web-qa") is False


async def test_set_limits_stores_guild_scoped_values(admin_env) -> None:
    skill, _, settings, make_ctx = admin_env
    ctx = make_ctx("admin1", guild_id="g1")

    result = await skill.set_limits(ctx, monthly_cost_cap=25.0, player_cooldown_seconds=5)

    assert result.error is None
    assert await settings.get_value(
        scope="guild", scope_id="g1", skill="admin", name="월 비용 상한", default=0.0
    ) == 25.0
    assert await settings.get_value(
        scope="guild", scope_id="g1", skill="admin", name="플레이어 쿨다운", default=0
    ) == 5


async def test_usage_returns_empty_placeholder(admin_env) -> None:
    skill, _, _, make_ctx = admin_env
    result = await skill.usage(make_ctx("admin1"))

    assert result.error is None
    assert result.data["calls"] == 0
```

- [ ] **Step 5: 테스트 실패 확인**

```bash
uv run pytest skills/admin/tests/test_admin.py -v
```

Expected: FAIL (`skills/admin/skill.py` 없음)

- [ ] **Step 6: `skill.py` 구현**

```python
# skills/admin/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import PlayerRef, ToolContext, ToolResult
from services.common.players import Player


@tool(slash="관리 사용량")
async def usage(ctx: ToolContext) -> ToolResult:
    """기간별 LLM 호출 수, 토큰, 추정 비용을 보여준다. LLM이 아직 없어 자리만 채운다."""
    return ToolResult(data={"calls": 0, "tokens": 0, "estimated_cost_usd": 0.0})


@tool(slash="관리 스킬")
async def skills(ctx: ToolContext) -> ToolResult:
    """등록된 스킬, 켜짐 여부, 등록 실패한 스킬과 이유를 보여준다."""
    snapshot = ctx.registry_snapshot
    if snapshot is None:
        return ToolResult(error="스킬 현황을 아직 알 수 없다")
    lines = [f"- {s.name} ({s.game}, v{s.version})" for s in snapshot.skills]
    lines += [f"- {name} 등록 실패: {reason}" for name, reason in snapshot.failures.items()]
    return ToolResult(
        data={"enabled": [s.name for s in snapshot.skills], "failures": snapshot.failures},
        display_mode="optional",
    )


@tool(slash="관리 스킬설정", mutates=True, confirm="always")
async def toggle_skill(ctx: ToolContext, skill_name: str, enabled: bool) -> ToolResult:
    """이 서버에서 스킬을 켜거나 끈다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 쓸 수 있다")
    await ctx.settings.set_skill_enabled(
        guild_id=ctx.guild_id, skill=skill_name, enabled=enabled, updated_by=ctx.player_id
    )
    return ToolResult(data={"skill": skill_name, "enabled": enabled})


@tool(slash="관리 태그부여", mutates=True, confirm="always")
async def grant_tag(ctx: ToolContext, target: PlayerRef, tag: str) -> ToolResult:
    """부여 태그·스킬 태그를 붙인다. 상태 태그는 거부한다."""
    assert isinstance(target, Player)
    try:
        await ctx.players.grant_tag(target.id, tag, source=f"admin:{ctx.player_id}")
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(data={"player_id": target.id, "granted": tag})


@tool(slash="관리 태그회수", mutates=True, confirm="always")
async def revoke_tag(ctx: ToolContext, target: PlayerRef, tag: str) -> ToolResult:
    """부여 태그·스킬 태그를 뗀다. 상태 태그는 거부하고, 마지막 admin은 못 뗀다."""
    assert isinstance(target, Player)
    if tag == "admin":
        holders = await ctx.players.players_with_tag("admin")
        if target.id in holders and len(holders) <= 1:
            return ToolResult(error="마지막 관리자는 뗄 수 없다")
    try:
        await ctx.players.revoke_tag(target.id, tag)
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(data={"player_id": target.id, "revoked": tag})


@tool(slash="관리 상한", mutates=True, confirm="always")
async def set_limits(ctx: ToolContext, monthly_cost_cap: float, player_cooldown_seconds: int) -> ToolResult:
    """월 비용 상한과 플레이어별 쿨다운을 저장한다. 적용(한도 집행)은 6단계다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 쓸 수 있다")
    await ctx.settings.set_value(
        scope="guild", scope_id=ctx.guild_id, skill="admin", name="월 비용 상한",
        value=monthly_cost_cap, updated_by=ctx.player_id,
    )
    await ctx.settings.set_value(
        scope="guild", scope_id=ctx.guild_id, skill="admin", name="플레이어 쿨다운",
        value=player_cooldown_seconds, updated_by=ctx.player_id,
    )
    return ToolResult(data={"monthly_cost_cap": monthly_cost_cap, "player_cooldown_seconds": player_cooldown_seconds})
```

(테스트는 `target=target`처럼 `Player` 객체를 바로 넘긴다 — `PlayerRef`는 모델/슬래시 경로에서 코어가 원문을 해석한 "자리"일 뿐이고, 함수가 실제로 받는 타입은 `Player`다. 단위 테스트는 해석기를 거치지 않고 함수를 직접 부르므로 이미 해석된 값을 넣는다.)

- [ ] **Step 7: 테스트 통과 확인**

```bash
uv run pytest skills/admin/tests/test_admin.py -v
```

Expected: PASS (7 tests)

- [ ] **Step 8: admin 태그 없는 사람에게 막히는지 — 레지스트리/실행기 수준 테스트**

```python
# skills/admin/tests/test_admin.py 에 추가
from core.executor import ToolExecutor
from core.registry import SkillRegistry
from core.resolvers import ResolverRegistry
from core.tool_types import PlayerRef as _PlayerRef  # 해석기 등록용


async def test_admin_skill_blocked_without_admin_tag() -> None:
    conn = await aiosqlite.connect(":memory:")
    for migration in _CORE_MIGRATIONS:
        await conn.executescript(migration)
    await conn.commit()
    players = PlayersService(conn)

    registry = SkillRegistry([Path("skills/admin")])
    registry.load()
    assert registry.failures() == {}

    resolvers = ResolverRegistry()
    resolvers.register(_PlayerRef, players)
    executor = ToolExecutor(
        resolvers=resolvers, tag_provider=players, tag_definitions=registry.tag_definitions(),
        services={"players": players, "settings": SettingsService(conn)},
    )

    [(skill, spec)] = [(s, sp) for s, sp in registry.tool_specs() if sp.name == "usage"]
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(await players.tags_for(player.id)), services={})

    outcome = await executor.invoke(spec, ctx, {}, skill_tags=skill.manifest.tags, skill_name="admin")
    assert outcome.result is not None
    assert outcome.result.error == "모르는 명령이에요"

    await players.grant_tag(player.id, "admin", source="test")
    ctx2 = ToolContext(player_id=player.id, tags=frozenset(await players.tags_for(player.id)), services={})
    outcome2 = await executor.invoke(spec, ctx2, {}, skill_tags=skill.manifest.tags, skill_name="admin")
    assert outcome2.result is not None
    assert outcome2.result.error is None

    await conn.close()
```

- [ ] **Step 9: 테스트 통과 확인**

```bash
uv run pytest skills/admin/tests/test_admin.py -v
```

Expected: PASS (8 tests)

- [ ] **Step 10: 전체 회귀 확인**

```bash
uv run pytest -v
```

Expected: PASS

- [ ] **Step 11: 커밋**

```bash
git add core/registry_snapshot.py core/tool_types.py core/slash.py core/bot.py \
  tests/core/test_registry_snapshot.py skills/admin
git commit -m "skills: add admin (usage placeholder, skill toggle, tag grant/revoke, limits)"
```

---

## Task 18: 전체 조립 — 남은 서비스를 실행기에 연결하고 전체 검증

**Files:**
- Modify: `core/bot.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml` (http 캐시 파일도 `data/` 볼륨 안에 있는지 확인)

**Interfaces:**
- Consumes: 1~17 Task의 모든 서비스/스케줄러/스킬
- Produces: 기동 시 `ToolExecutor.services`에 `players, settings, memory, secrets, notify, arcades, http, yuni`가 전부 들어간 완성된 `build_client`.

- [ ] **Step 1: `core/bot.py`에 남은 서비스 와이어링**

```python
# core/bot.py: import 추가
from services.common.arcades import ArcadesService
from services.common.http import build_http_service
from services.common.memory import MemoryService
from services.common.notify import NotifyService
from services.common.secrets import SecretsService
from services.common.yuni import YuniService

# build_client 안, settings_service = SettingsService(conn) 다음에 추가
    memory_service = MemoryService(conn)
    arcades_service = ArcadesService(conn)
    secrets_service = SecretsService(config.cookie_encryption_key) if config.cookie_encryption_key else None

    class _DiscordGateway:
        def __init__(self, discord_client: discord.Client) -> None:
            self._client = discord_client

        async def send_channel_message(self, channel_id: str, content: str) -> None:
            channel = self._client.get_channel(int(channel_id))
            if channel is not None:
                await channel.send(content)  # type: ignore[union-attr]

        async def send_dm(self, discord_id: str, content: str, *, view: discord.ui.View | None = None) -> None:
            user = await self._client.fetch_user(int(discord_id))
            await user.send(content, view=view)  # type: ignore[arg-type]

    # client는 아래에서 만들어지므로, 게이트웨이는 client 생성 뒤 notify_service에 넣는다.
```

(`YuniClient` 생성 이후로 옮겨 적는다 — 순서가 중요하다.)

```python
# client = YuniClient(...) 다음에 추가
    notify_service = NotifyService(
        gateway=_DiscordGateway(client), players=players, settings=settings_service, threads=ThreadManager()
    )
    http_service = await build_http_service(cache_db_path=config.db_path.parent / "http_cache.sqlite")
    yuni_service = YuniService()

    services["memory"] = memory_service
    services["arcades"] = arcades_service
    services["notify"] = notify_service
    services["http"] = http_service
    services["yuni"] = yuni_service
    if secrets_service is not None:
        services["secrets"] = secrets_service

    resolvers.register(ArcadeRef, arcades_service)
```

(`from core.threads import ThreadManager`와 `from core.tool_types import ArcadeRef`도 import에 더한다. `services` 딕셔너리는 Task 6에서 만들고 Task 9에서 `settings`를 더한 바로 그 딕셔너리다 — `executor = ToolExecutor(..., services=services, ...)`가 이미 그 딕셔너리를 참조로 들고 있으므로, `executor` 생성 **전에** 위 할당들을 끝내야 한다. 순서를 맞추기 위해 `build_client` 안의 순서를 다음으로 정리한다: 레지스트리 로드 → 계약 검사 → 마이그레이션 → `players`/`settings`/`memory`/`arcades`/`secrets` 생성 → `player_data`/`event_bus`/`scheduler` 생성 → `services` 딕셔너리 채우기 → `resolvers` 등록(`PlayerRef`, `ArcadeRef`) → `executor` 생성 → `client` 생성 → `notify`/`http`/`yuni` 생성해 `services`에 추가 → 슬래시 트리 조립 → 스케줄러 동기화/캐치업.)

- [ ] **Step 2: `.env.example` 최종 확인**

이미 Task 1에서 `ADMIN_DISCORD_IDS`, `QUIET_HOURS_START/END`, `COOKIE_ENCRYPTION_KEY`를 더했다. 빠진 게 없는지 `cat .env.example`로 확인하고, 실제 운영에 쓸 `.env`에도 (커밋하지 않고) 같은 키를 채운다. `COOKIE_ENCRYPTION_KEY`는 아래로 하나 생성해 둔다(4단계에서 실제로 쓰기 전까지는 비워 둬도 기동엔 지장이 없다 — `secrets_service`를 조건부로 만든 이유다):

```bash
uv run python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- [ ] **Step 3: `docker-compose.yml`의 볼륨 확인**

`data/` 전체가 볼륨으로 마운트돼 있으면 `data/http_cache.sqlite`도 같이 보존된다(캐시라 지워도 안전하지만, 지울 필요가 없다는 뜻이다). 이미 그렇다면 고칠 것이 없다 — 확인만 하고 다음 스텝으로 넘어간다.

- [ ] **Step 4: 전체 테스트, 린트, 타입 검사**

```bash
uv run pytest -v
uv run ruff check .
uv run basedpyright
```

Expected: 테스트 전부 PASS, `ruff`/`basedpyright` 모두 깨끗함(0 errors). 타입 오류나 린트 경고가 나오면 그 자리에서 고친다 — 이 Task는 "전체가 깨끗하다"를 확인하는 것 자체가 완료 조건이다.

- [ ] **Step 5: 커밋**

```bash
git add core/bot.py .env.example
git commit -m "core: wire remaining common services into the executor for stage 3"
```

---

## 끝나면 할 일 (이 계획의 Task는 아님)

1. **서브에이전트 대조 검토.** 구현이 끝나면 서브에이전트를 하나 띄워 `docs/design/skills/account-link.md`, `docs/design/skills/admin.md`, `docs/design/00-core.md`의 "데이터, 권한, 비밀 값" 절과 실제 구현(특히 `skills/account-link/`, `skills/admin/`, `core/permissions.py`의 `STATUS_ONLY_TAGS`, `services/common/players.py`의 태그 처리)을 한 줄씩 대조해, 다르게 만든 부분을 전부 보고받는다. 이 계획의 "합의된 단순화" 9개 항목이 실제로 그 문서들과 충돌하는지도 함께 확인한다.
2. **디스코드 테스트 서버에서 확인할 순서.** (통과 기준 7번) 아래 순서를 그대로 안내한다:
   - 테스트 서버에 두 개의 디스코드 테스트 계정으로 접속한다.
   - 계정 A에서 `/연결 시작` → 코드 확인.
   - 계정 B에서 `/연결 참여`에 그 코드 입력 → (B에 데이터가 없으면) A가 DM으로 받는 확인 버튼과 B 자신의 응답에 뜬 확인 버튼을 각각 누른다.
   - `/연결 목록`을 A 또는 B 어느 쪽에서 실행해도 같은 목록(두 계정)이 나오는지 확인한다.
   - `.env`의 `ADMIN_DISCORD_IDS`에 계정 A의 디스코드 ID를 넣고 재시작 → A에게서만 `/관리` 계열 명령이 보이는지 확인한다(B에게는 "모르는 명령"처럼 보인다).
   - `/관리 스킬설정`으로 아무 스킬(예: `web-qa`— 아직 없으면 `practice` 픽스처가 배포 대상이 아니므로 `account-link`로)을 이 서버에서 꺼 보고, 그 서버의 슬래시 명령이 실제로 막히는지 확인한다.
   - 컨테이너를 재시작(`docker compose restart`)한 뒤 `/설정 보기`로 이전에 바꾼 설정값이 그대로인지 확인한다.
   - 새벽 정지 시간대(`QUIET_HOURS_START/END`)를 지금 시각을 포함하도록 잠깐 바꿔 재시작한 뒤, 로그에서 예약이 정지 시간대를 피해 잡히는지 확인한다(실제 예약을 쓰는 스킬이 아직 없으므로, 이 확인은 `core/scheduler.py`의 단위 테스트가 이미 한 것의 수동 재확인에 가깝다 — 9단계에서 `chunithm-goals`가 생기면 진짜 예약으로 다시 확인한다).
