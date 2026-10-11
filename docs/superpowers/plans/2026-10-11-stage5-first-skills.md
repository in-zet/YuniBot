# 5단계: 첫 스킬 (chunithm-account, chunithm-song, chunithm-border, chunithm-terms) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 00-core.md "작업 순서" 5단계를 완성한다 — `chunithm-account`, `chunithm-song`, `chunithm-border`, `chunithm-terms` 네 스킬을 만들고, 모든 도구를 슬래시 명령으로 확인한다(LLM 경로는 6단계).

**Architecture:** 4단계에서 만든 `songdb`/`records`/`terms` 서비스는 이 네 스킬이 실제로 쓰기엔 메서드가 모자란다(곡 메타데이터 일부 누락, 서버별 별칭 저장 없음, 점수 유틸리티가 서비스에 노출 안 됨, 개념 용어/서버 은어 다루는 메서드 없음). 스킬을 만들기 전에 서비스를 먼저 확장한다(Task 2~6). 또 코어의 `@slash_only`는 지금 모든 슬래시 콜백을 먼저 `defer()`하는데, `/로그인` 입력창(모달)은 인터랙션의 "최초 응답"으로만 열 수 있어 defer 이후에는 열 수 없다 — 이 역시 스킬을 쓰기 전에 코어를 고친다(Task 1).

**Tech Stack:** Python 3.14, discord.py 2.7(app_commands, discord.ui.Modal), aiosqlite, msgspec은 안 씀(이 스킬들은 표준 라이브러리 dict/dataclass로 충분), pytest-asyncio.

**Spec:** `docs/design/00-core.md`, `docs/design/skills/chunithm-account.md`, `docs/design/skills/chunithm-song.md`, `docs/design/skills/chunithm-border.md`, `docs/design/skills/chunithm-terms.md`. 구현 세션은 이 네 문서와 `CLAUDE.md`를 함께 읽는다.

## Global Constraints

- 표준 인자 타입(`PlayerRef`, `SongQuery`, `ChartRef`, `RankRef`, `DifficultyRef`, `LampRef`, `ScoreRef`)은 `core/bot.py`가 이미 전역으로 등록해 뒀다 — 스킬의 `manifest.services`에 적지 않아도 도구 인자로 쓸 수 있다(해석은 코어의 `ResolverRegistry`가 한다, `ctx` 서비스 접근과는 별개 경로).
- 스킬은 `manifest.services`에 적은 서비스만 `ctx.<서비스>`로 쓴다. 이 규칙은 `core/registry.py`의 `_find_undeclared_service_usage`가 로드 시점에 정적으로 검사한다.
- 보더는 저스티스 0, 미스 0 기준의 허용 어택 개수 하나만 쓴다. 소수 둘째 자리까지 쓰고 셋째 자리에서 버린다(`ROUND_DOWN`). 남은 어택도 버림이고, 남은 점수를 함께 쓴다.
- 노트 수가 없는 채보는 "계산 불가"로 표시한다.
- 랭크·난이도·램프·점수는 `terms` 서비스(코어의 표준 타입)로만 해석한다. 스킬 코드가 직접 숫자를 비교/변환하지 않는다.
- SQL은 직접 쓴다(ORM 없음). 마이그레이션은 `NNN_설명.sql`, 한 번 적용한 파일은 고치지 않고 다음 번호 파일을 더한다.
- 스킬 테이블 접두사는 스킬 이름에서 `chunithm-`을 뺀 나머지의 하이픈을 밑줄로 바꾼 것이다. 이번 4개 스킬 중 스킬 소유 테이블을 새로 만드는 것은 없다(모두 서비스 소유 테이블을 통해 데이터를 다룬다).
- 이 단계는 LLM이 없다 — `@tool`은 전부 등록하되 슬래시 경로로만 확인한다.
- 비밀 값(clal 쿠키)은 `secrets`/`chunithm_net` 서비스 밖으로 평문이 나가지 않는다. 로그, 예외 메시지, LLM 입력 어디에도 넣지 않는다.
- 작업을 마치면 커밋해도 되는지 묻지 않고 바로 커밋한다(CLAUDE.md).

## 결정 사항 (이 세션에서 정한 것 — 구현 세션은 다시 묻지 않는다)

1. **chunithm-terms 초기 개념 용어 목록**: 설계서의 "미확인 사항"대로 검증된 출처가 없다. 사용자가 "몇 개 임시로 채워 넣기"를 선택했다 — `저스티스 크리티컬`(판정), `안미츠/비비기`(기술, 설계서 예시 문구를 그대로 씀), `상수`(개념), `오버파워`(개념) 4개를 `terms.yaml`에 "임시 등록, 검토 필요" 주석과 함께 넣는다(Task 5). 랭크/난이도/램프 값 용어는 이미 4단계에서 다 채워져 있고(랭크 14개, 난이도 6개, 램프 3개 — AJC/ALL JUSTICE/FULL COMBO) 손대지 않는다.
2. **`chunithm.login.expired` 핸들러와 EventBus 호출 규약 불일치**: `core/decorators.py`의 `@on`은 핸들러 첫 인자 이름이 `ctx`여야 한다고 검사하고, 실제로 `tests/core/test_decorators.py`의 예시도 `(ctx: ToolContext, payload: dict) -> None` 2개 인자다. 그런데 `core/events.py`의 `EventBus._run_notify_handler`/`_run_gather_handler`는 등록된 핸들러를 `handler.fn(payload)` **한 개 인자**로만 부르고, `core/bot.py`의 `_register_event_handlers`는 `handler.fn`을 그대로(래핑 없이) 등록한다. 즉 지금 코드베이스에는 실제로 2-인자 `@on` 핸들러를 엔드투엔드로 호출하는 경로가 없다(지금까지 `@on`을 쓰는 스킬이 하나도 없어서 드러나지 않았다). `chunithm-account`가 이걸 쓰는 첫 스킬이 된다.
   - **이번 단계에서 하는 일**: `on_login_expired(ctx, payload)`를 설계서/데코레이터 규약대로 2-인자로 작성하고, 테스트는 함수를 직접 호출해 로직만 검증한다(아래 Task 7).
   - **이번 단계에서 안 하는 일**: `core/bot.py`의 `_register_event_handlers`/`EventBus` 호출 경로를 고쳐 실제 디스코드 이벤트 발생 시 `ctx`를 올바르게 만들어 넘기는 일. 이걸 트리거하는 유일한 코드(`ChunithmNetService.fetch_for_sync`)는 7단계 `chunithm-sync`에서만 불린다 — 지금 고치면 "지금 단계 밖의 기능"을 만지게 된다. **7단계를 시작하기 전에 이 글래핑 문제를 먼저 고쳐야 한다** — 그 전까지는 로그인 만료 DM이 실제로 발송되지 않는다(코드는 올바르지만 배선이 안 됐다). 7단계 계획에 반드시 적어 둘 것.
3. **`/로그인` 모달과 `@slash_only`의 defer**: `core/slash.py`의 `_dispatch`는 모든 슬래시 콜백에서 도구 본문을 부르기 전에 무조건 `interaction.response.defer()`를 부른다. 디스코드는 모달을 인터랙션의 "최초 응답"으로만 열 수 있어 defer 뒤에는 못 연다. `@slash_only` 함수가 `ToolResult` 대신 `discord.ui.Modal`을 돌려주면 코어가 defer 없이 바로 `send_modal`하도록 `core/slash.py`를 고친다(반환 타입 힌트로 분기 — `core/events.py`의 `handler_kind`와 같은 방식, 데코레이터에 새 옵션을 안 늘린다). Task 1.
4. **`songdb`/`records`/`terms` 서비스 확장은 "다른 스킬을 고치는 것"이 아니다**: 00-core.md의 "코어나 다른 스킬을 고치지 않고 스킬을 더할 수 있어야 한다"는 스킬·코어 얘기고, 서비스는 "두 개 이상의 스킬이 쓰거나 데이터 일관성을 지켜야 하면 두는" 계층이라 새 스킬이 필요한 메서드를 추가하며 자라는 게 정상이다. 아래 Task 2~6이 전부 이 확장이다. 서비스 생성자에 새 매개변수(`conn`)를 추가할 때는 기존 호출부(`SongDbService()`, `TermsService()` — 기존 테스트 다수가 인자 없이 생성)가 깨지지 않게 전부 **키워드, 기본값 `None`**으로 추가한다.
5. **곡 별칭(서버별 한국어 별칭) 저장 위치**: 00-core.md "DB 구분" 표가 "별칭"을 "츄니즘 서비스" 소유로 명시한다(`chunithm-song` 스킬 소유가 아니다 — `SongQuery` 해석기가 `songdb` 서비스 하나뿐이고 모든 스킬이 공유하기 때문). `migrations/core/011_songdb_aliases.sql`에 `songdb_aliases` 테이블을 만들고 `SongDbService`가 관리한다. 스코프는 서버별(`guild_id`)이다 — 플레이어별이 아니다(오락실 별칭과 다르다. 오락실은 설계서가 명시적으로 "플레이어별"이라고 적었고, 곡 별칭은 "서버별"이라고 적었다).
6. **곡 별칭 해석 우선순위**: `SongDbService.resolve()`는 ①정확한 제목 ②이 서버의 저장된 별칭 + 시드가 주는 전역 별칭(둘 다 대소문자 무시, 같은 우선순위 묶음) ③부분 일치 후보 나열, 순서로 찾는다. 서버 밖(DM)에서는 ②에서 서버 별칭 부분만 건너뛴다.
7. **`chunithm-border.remaining`에서 그 채보를 플레이한 적이 없으면**: 점수 0으로 계산하지 않고 "그 채보를 아직 플레이한 기록이 없다" 오류를 돌려준다(없는 걸 0으로 지어내지 않는다 — CLAUDE.md "바뀌면 안 되는 규칙"의 숫자 규칙과 같은 생각).
8. **`chunithm-border`의 기본 4랭크(`lookup`에서 `rank`를 안 줬을 때)는 스킬 코드에 상수로 박는다**: `chunithm-border.md` "계산" 표가 이미 SSS+/SSS/SS+/SS 네 기준 점수를 명시해 두었다 — `terms` 서비스를 또 부르지 않고 그 표의 숫자를 그대로 모듈 상수로 쓴다(설계서 자체가 고정값으로 제시했다).
9. **이벤트 payload 형식은 msgspec 구조체가 아니라 평범한 dict로 통일한다**: 00-core.md는 "내용 형식은 내는 쪽이 msgspec 구조체로 정한다"고 적었지만, 실제로 이미 존재하는 이벤트(`players.linked`, `chunithm.login.expired`)는 전부 평범한 dict다. 새로 어긋나는 관행을 만들지 않고 기존 방식을 따른다. `chunithm.song.viewed`도 dict(`{"player_id", "song_title", "difficulty"}`)로 낸다.
10. **`chunithm.song.viewed`를 받는 스킬이 없어도 낸다**: `ctx.event_bus.emit_gather(...)`를 부르되, 지금은 아무도 안 들으니 빈 리스트가 돌아온다. 돌아온 조각을 표시물에 합치는 로직은 실제 소비자가 생기는 7단계에서 채운다(지금은 호출만 하고 결과를 쓰지 않는다 — 없는 소비자를 위한 렌더링 코드를 지금 미리 만들지 않는다).
11. **상수 범위 검색(`find_by_const`) 결과가 많을 때**: 전부 돌려주되 표시(embed)는 상위 25개까지만 나열하고 나머지는 "...외 N개"로 줄인다(디스코드 embed 필드 제한을 고려한 임의 상한 — 설계서에 수치가 없어 직접 정한다).
12. **sdvx.in 링크는 vendored `chuni_penguin.utils.formatting.sdvxin_link`를 그대로 재사용한다**: 그 함수는 `TYPE_CHECKING` 가드 뒤의 타입만 SQLAlchemy 모델(`SdvxinChartView`)을 참조하고 런타임에는 `.id`/`.difficulty`/`.end_index` 속성만 읽는 순수 문자열 함수라, `SimpleNamespace`로 흉내 낸 객체를 넘겨 그대로 쓸 수 있다(third_party를 고치지 않고 재사용 — records_calc.py가 `calculate_rating`을 재사용한 것과 같은 패턴). 매핑이 없을 때의 유튜브 링크는 설계서가 "곡명 + 譜面確認" 검색어를 요구하는데 vendored `yt_search_link`는 그 문구를 안 붙이므로, 이건 재사용하지 않고 `songdb.py`에 짧은 전용 함수를 새로 쓴다.

## Review Focus

- **상수를 모르는 채보의 보더/남은 어택**: `lookup`/`remaining`이 `note_count is None`일 때 "계산 불가"로만 답하고 0이나 음수 같은 가짜 숫자를 만들지 않는지(Task 8에서 테스트).
- **이미 목표 랭크를 넘은 상태에서 `remaining`을 물었을 때**: 음수 남은 점수를 "0.00어택, 음수 남은 점수"로 그대로 보여주지 말고 "이미 달성" 의미가 드러나게 처리하는지, records_calc의 `remaining_to_target`이 음수/0을 돌려줄 때 스킬이 숫자를 조작하지 않고 그대로 쓰는지(Task 8).
- **다른 곡에 이미 쓰인 한국어 별칭을 또 등록하려 할 때**: `alias_add`가 거절하고 기존 매핑을 덮어쓰지 않는지, 서버가 다르면 같은 별칭이 다른 곡을 가리켜도 되는지(Task 2, 7).
- **별칭을 추가한 사람이 아닌 다른 일반 플레이어가 `alias_remove`를 부를 때**: admin이 아니면 거절되고, 실제로 지워지지 않는지(Task 7).
- **용어 설명(`explain`)에서 전역 값 용어(랭크 별칭 등)와 이 서버에서 승인된 서버 은어가 같은 글자로 겹칠 때**: 전역이 이긴다고 설계서가 적었다 — 전역 먼저 찾고 없을 때만 서버 은어를 보는지(Task 6, 9).

---

## Task 1: 코어 — `@slash_only`가 `discord.ui.Modal`을 돌려주면 입력창을 연다

**Files:**
- Modify: `core/slash.py`
- Modify: `core/executor.py`
- Test: `tests/core/test_slash.py`

**Interfaces:**
- Consumes: 기존 `SlashPlanEntry`, `ToolExecutor`(`.services`, `.tag_provider`, `.event_bus`), `core.permissions.TagRequirement`/`check_tags`.
- Produces: `ToolExecutor.settings` 프로퍼티(읽기 전용, `SkillToggleProvider | None`). `core/slash.py`의 `_build_command`가 `discord.ui.Modal`을 반환 타입으로 선언한 `@slash_only` 함수를 자동으로 모달 전용 콜백으로 빌드한다 — 호출하는 쪽(`attach_to_tree`, `build_slash_plan`)의 공개 시그니처는 안 바뀐다.

- [ ] **Step 1: 실패하는 테스트부터 쓴다 — 모달 반환 슬래시 전용 명령이 defer 없이 바로 열려야 한다**

`tests/core/test_slash.py`에 추가:

```python
class _FakeModal(discord.ui.Modal, title="테스트 모달"):
    pass


@slash_only("모달테스트")
async def open_test_modal(ctx: ToolContext) -> discord.ui.Modal:
    """테스트용 입력창을 연다."""
    return _FakeModal()
```

(파일 맨 위 `@tool` 정의들 옆에 두고, `from core.decorators import slash_only`를 import 목록에 추가한다.)

`_FakeResponse`에 메서드 추가:

```python
    async def send_modal(self, modal: Any) -> None:
        self._owner.calls.append({"modal": modal})
```

새 테스트 함수(파일 뒤쪽, `_FakeInteraction`/`practice_tree` 정의 이후):

```python
async def test_modal_returning_slash_only_opens_modal_without_deferring() -> None:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION)
    await conn.commit()
    players = PlayersService(conn)
    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=players, tag_definitions={}, services={"players": players},
    )
    plan = build_slash_plan(
        [(_FakeSkill("x"), open_test_modal.__yuni_slash_only__)]  # pyright: ignore[reportFunctionMemberAccess, reportArgumentType]
    )
    tree: discord.app_commands.CommandTree[Any] = discord.app_commands.CommandTree(
        discord.Client(intents=discord.Intents.none())
    )
    attach_to_tree(tree, plan, executor=executor, db=_NO_DB, registry_snapshot=_NO_SNAPSHOT)

    command = tree.get_command("모달테스트")
    assert command is not None
    interaction = _fake(1)
    await command.callback(interaction)  # pyright: ignore[reportCallIssue]

    [call] = interaction.calls
    assert isinstance(call.get("modal"), _FakeModal)
    assert interaction.response.is_done() is False  # defer를 부르지 않았다
    await conn.close()
```

- [ ] **Step 2: 테스트를 돌려 실패를 확인한다**

Run: `uv run pytest tests/core/test_slash.py -k modal -v`
Expected: FAIL — `_FakeResponse`에 `send_modal`은 추가했지만 아직 `core/slash.py`가 모달을 보내지 않으므로(지금은 `defer()`를 부르고 `ToolResult`를 조립하려다 `await self.clal_input...` 같은 속성 접근 없이도, 반환값이 `discord.ui.Modal`이라 `render_result`/`respond`가 깨지거나 적어도 `send_modal`이 전혀 호출되지 않아 `interaction.calls`가 비어 `[call] = interaction.calls`에서 `ValueError`).

- [ ] **Step 3: `core/executor.py`에 `settings` 프로퍼티를 추가한다**

`ToolExecutor` 클래스에 다른 프로퍼티들(`tag_provider`, `services`, `event_bus`, `player_data`) 옆에 추가:

```python
    @property
    def settings(self) -> SkillToggleProvider | None:
        """core/slash.py의 모달 콜백이 일반 _dispatch와 같은 "스킬 꺼짐" 검사를 하기 위해 쓴다."""
        return self._settings
```

- [ ] **Step 4: `core/slash.py`에 모달 분기를 추가한다**

import 추가(파일 맨 위):

```python
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, get_type_hints

from core.permissions import TagRequirement, check_tags
```

(`typing` import 줄은 기존 `from typing import TYPE_CHECKING, Any`를 `get_type_hints`까지 포함하도록 고친다.)

`_build_command` 함수 맨 앞에 분기 추가:

```python
def _build_command(
    entry: SlashPlanEntry, *, executor: ToolExecutor, db: aiosqlite.Connection, registry_snapshot: RegistrySnapshot
) -> app_commands.Command[Any, Any, Any]:
    if _is_modal_tool(entry.spec.fn):
        return _build_modal_command(entry, executor=executor)

    schema = build_arg_schema(entry.spec.fn)
    # ... (기존 코드 그대로)
```

새 함수 두 개를 `_build_command` 앞에 추가:

```python
def _is_modal_tool(fn: Callable[..., Any]) -> bool:
    """@slash_only 함수가 ToolResult 대신 discord.ui.Modal을 돌려주면, 입력창을 여는
    명령으로 본다(core/events.py의 handler_kind와 같은 생각 — 반환 타입 힌트로 의도를
    추론해 데코레이터에 새 옵션을 늘리지 않는다)."""
    hints = get_type_hints(fn)
    ret = hints.get("return")
    return isinstance(ret, type) and issubclass(ret, discord.ui.Modal)


def _build_modal_command(
    entry: SlashPlanEntry, *, executor: ToolExecutor
) -> app_commands.Command[Any, Any, Any]:
    """모달은 인터랙션의 "최초 응답"으로만 열 수 있어(디스코드 제약), 보통 명령처럼
    먼저 defer()하면 다시는 열 수 없다 — 그래서 일반 _dispatch(먼저 defer)를 쓰지 않는다."""

    async def callback(interaction: discord.Interaction) -> None:
        if (
            executor.settings is not None
            and interaction.guild_id is not None
            and not await executor.settings.is_skill_enabled(
                guild_id=str(interaction.guild_id), skill=entry.skill_name
            )
        ):
            await interaction.response.send_message("모르는 명령이에요", ephemeral=True)
            return

        discord_id = str(interaction.user.id)
        players: PlayersService = executor.services["players"]  # pyright: ignore[reportAssignmentType]
        player = await players.player_for_discord(discord_id)
        is_new_player = player is None
        if player is None:
            player = await players.create_player()
            await players.link_account(player.id, discord_id)

        tags = frozenset(await executor.tag_provider.tags_for(player.id))
        requirement = TagRequirement(tags=entry.skill_tags, tags_any=entry.skill_tags_any)
        if not check_tags(requirement, tags):
            await interaction.response.send_message("모르는 명령이에요", ephemeral=True)
            return

        skill_services = {
            name: executor.services[name] for name in entry.skill_services if name in executor.services
        }
        ctx = ToolContext(
            player_id=player.id, tags=tags, services=skill_services,
            guild_id=str(interaction.guild_id) if interaction.guild_id else None,
            event_bus=executor.event_bus, discord_id=discord_id,
        )

        if is_new_player:
            await executor.event_bus.emit_notify("players.first_seen", {"player_id": player.id})

        modal = await entry.spec.fn(ctx)
        await interaction.response.send_modal(modal)

    return app_commands.Command(
        name=entry.path[-1],
        description=(entry.spec.fn.__doc__ or "").strip().splitlines()[0][:100],
        callback=callback,
    )
```

- [ ] **Step 5: 테스트를 다시 돌려 통과를 확인한다**

Run: `uv run pytest tests/core/test_slash.py -v`
Expected: PASS (새 테스트 포함, 기존 테스트 전부 그대로 통과).

- [ ] **Step 6: 전체 테스트 + 린트 + 타입 검사**

Run: `uv run pytest -q && uv run ruff check . && uv run basedpyright`
Expected: 전부 통과.

- [ ] **Step 7: 커밋**

```bash
git add core/slash.py core/executor.py tests/core/test_slash.py
git commit -m "core: @slash_only가 discord.ui.Modal을 돌려주면 defer 없이 입력창을 연다"
```

---

## Task 2: `songdb` 서비스 — 곡 메타데이터 확장(BPM/수록 버전/레벨/sdvx.in) + 상수 범위 검색

**Files:**
- Modify: `services/chunithm/songdb.py`
- Modify: `services/chunithm/chart_ref.py`
- Test: `tests/services/chunithm/test_songdb.py`

**Interfaces:**
- Consumes: 기존 `SongDbService`, `ChartRecord`, `SongRecord`.
- Produces: `ChartRecord.level: str | None`, `ChartRecord.sdvxin: SdvxinMapping | None`, `SongRecord.bpm: float | None`, `SongRecord.version: str | None`. `SongDbService.find_by_const(min_const, max_const, difficulty) -> list[ConstRangeHit]`. `SongDbService.chart_link(song, chart) -> ChartLinkInfo`. 모듈 레벨 공개 상수 `LONG_TO_SHORT_DIFFICULTY: dict[str, str]`(기존 `chart_ref.py`의 `_LONG_TO_SHORT`를 여기로 옮긴 것) — `chunithm-song`/`chunithm-border` 스킬과 `chart_ref.py`가 공유해서 쓴다.

- [ ] **Step 1: 실패하는 테스트 — 메타데이터가 로드되는지, 상수 범위 검색이 되는지**

`tests/services/chunithm/test_songdb.py`에 추가:

```python
async def test_seed_loads_bpm_version_level_and_sdvxin(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.bpm is not None
    assert song.version is not None
    mas = song.charts["MAS"]
    assert mas.level is not None


async def test_find_by_const_returns_sorted_hits_in_range(songdb: SongDbService) -> None:
    hits = songdb.find_by_const(min_const=12.0, max_const=13.0, difficulty="MAS")
    assert hits  # 로컬 시드에 12~13 상수의 MAS 채보가 있어야 한다
    consts = [hit.chart.const for hit in hits]
    assert consts == sorted(consts)
    assert all(12.0 <= c <= 13.0 for c in consts)


async def test_find_by_const_excludes_unavailable_songs(songdb: SongDbService) -> None:
    hits = songdb.find_by_const(min_const=0.0, max_const=99.0, difficulty="BAS")
    assert all(hit.song.title != "天体観測" for hit in hits)  # 국제판 미수록곡은 제외


async def test_chart_link_uses_sdvxin_mapping_when_present(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    link = songdb.chart_link(song, song.charts["MAS"])
    assert link.has_mapping is True
    assert "sdvx.in" in link.url


async def test_chart_link_falls_back_to_youtube_without_mapping(songdb: SongDbService) -> None:
    from services.chunithm.songdb import ChartRecord, SongRecord

    fake_song = SongRecord(title="테스트곡", aliases=(), artist="", charts={})
    fake_chart = ChartRecord(difficulty="MAS", const=10.0, note_count=1000, sdvxin=None)

    link = songdb.chart_link(fake_song, fake_chart)

    assert link.has_mapping is False
    assert "youtube.com" in link.url
```

(위 "placeholder 아님" 문단은 실제 파일에 쓰지 않는다 — 최종 코드는 두 번째 버전 하나만 남긴다.)

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `uv run pytest tests/services/chunithm/test_songdb.py -v`
Expected: FAIL — `ChartRecord`/`SongRecord`에 `level`/`sdvxin`/`bpm`/`version` 필드가 없고, `find_by_const`/`chart_link`도 없다.

- [ ] **Step 3: `services/chunithm/songdb.py` 수정**

`ChartRecord`/`SongRecord` 선언 수정:

```python
@dataclass(slots=True, kw_only=True)
class SdvxinMapping:
    id: str
    end_index: str = ""


@dataclass(slots=True, kw_only=True)
class ChartRecord:
    difficulty: str
    const: float | None
    note_count: int | None
    is_const_unknown: bool = False
    is_available: bool = True
    level: str | None = None
    sdvxin: SdvxinMapping | None = None


@dataclass(slots=True, kw_only=True)
class SongRecord:
    title: str
    aliases: tuple[str, ...]
    artist: str
    charts: dict[str, ChartRecord] = field(default_factory=dict)
    is_new: bool = False
    bpm: float | None = None
    version: str | None = None
```

`_load_seed`에서 `SongRecord` 생성 시 `bpm=entry.get("bpm"), version=entry.get("version")` 추가, `ChartRecord` 생성 시 `level=c.get("level")`과 sdvxin 매핑 추가:

```python
        for c in entry.get("charts", []):
            code = c["difficulty"]
            key = (title, code)
            if key in winner_is_available and not (
                entry_available and not winner_is_available[key]
            ):
                continue
            sdvxin_data = c.get("sdvxin")
            song.charts[code] = ChartRecord(
                difficulty=code, const=c.get("const"), note_count=_note_count(c),
                is_available=entry_available, level=c.get("level"),
                sdvxin=(
                    SdvxinMapping(id=str(sdvxin_data["id"]), end_index=sdvxin_data.get("end_index") or "")
                    if sdvxin_data
                    else None
                ),
            )
            winner_is_available[key] = entry_available
```

(`SongRecord(...)` 생성 줄에 `bpm=entry.get("bpm"), version=entry.get("version")`를 더한다.)

파일 맨 위, `_MAX_CANDIDATES` 옆에 공유 매핑 추가(이전에 `chart_ref.py`에만 있던 것을 여기로 옮긴다):

```python
# terms.resolve_difficulty()가 돌려주는 정식 긴 이름("MASTER")을 이 서비스가 쓰는
# 짧은 코드("MAS")로 바꾼다. chart_ref.py도 이 매핑을 그대로 쓴다(중복 정의하지 않는다).
LONG_TO_SHORT_DIFFICULTY: dict[str, str] = {
    "BASIC": "BAS", "ADVANCED": "ADV", "EXPERT": "EXP",
    "MASTER": "MAS", "ULTIMA": "ULT", "WORLD'S END": "WE",
}
```

`SongDbService` 클래스에 메서드 추가:

```python
    def find_by_const(
        self, *, min_const: float, max_const: float, difficulty: str
    ) -> list[ConstRangeHit]:
        """상수 범위(양끝 포함)와 난이도 코드로, 지금 국제판에서 고를 수 있는 채보를
        상수 오름차순(같으면 제목순)으로 찾는다. difficulty는 짧은 코드("MAS" 등)다."""
        hits: list[ConstRangeHit] = []
        for title, song in self._songs.items():
            if title not in self._available_intl:
                continue
            chart = song.charts.get(difficulty)
            if chart is None or not chart.is_available or chart.const is None:
                continue
            if min_const <= chart.const <= max_const:
                hits.append(ConstRangeHit(song=song, chart=chart))
        hits.sort(key=lambda hit: (hit.chart.const, hit.song.title))
        return hits

    def chart_link(self, song: SongRecord, chart: ChartRecord) -> ChartLinkInfo:
        """채보 뷰어 링크. sdvx.in 매핑이 있으면 그 링크, 없으면 유튜브 검색 링크."""
        if chart.sdvxin is not None:
            from types import SimpleNamespace

            from chuni_penguin.utils.formatting import sdvxin_link

            view = SimpleNamespace(id=chart.sdvxin.id, difficulty=chart.difficulty, end_index=chart.sdvxin.end_index)
            return ChartLinkInfo(url=sdvxin_link(view), has_mapping=True)

        from urllib.parse import quote

        query = quote(f"{song.title} {chart.difficulty} 譜面確認")
        return ChartLinkInfo(url=f"https://www.youtube.com/results?search_query={query}", has_mapping=False)
```

새 데이터클래스 두 개를 `SongDbService` 선언 앞에 추가:

```python
@dataclass(slots=True, kw_only=True)
class ConstRangeHit:
    song: SongRecord
    chart: ChartRecord


@dataclass(slots=True, kw_only=True)
class ChartLinkInfo:
    url: str
    has_mapping: bool
```

- [ ] **Step 4: `services/chunithm/chart_ref.py`에서 중복 매핑 제거**

`_LONG_TO_SHORT` 딕셔너리 정의를 지우고 import로 대체:

```python
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY as _LONG_TO_SHORT
```

(기존에 `_LONG_TO_SHORT`를 쓰던 코드는 그대로 두되, 정의만 import로 바꾼다.)

- [ ] **Step 5: 테스트 재실행 → 통과 확인**

Run: `uv run pytest tests/services/chunithm/test_songdb.py tests/services/chunithm/test_chart_ref.py -v`
Expected: PASS.

- [ ] **Step 6: 린트 + 타입 검사**

Run: `uv run ruff check . && uv run basedpyright`
Expected: 통과.

- [ ] **Step 7: 커밋**

```bash
git add services/chunithm/songdb.py services/chunithm/chart_ref.py tests/services/chunithm/test_songdb.py
git commit -m "services(songdb): BPM/수록 버전/레벨/sdvx.in 매핑, 상수 범위 검색, 공유 난이도 매핑"
```

---

## Task 3: `songdb` 서비스 — 서버별 한국어 별칭 저장/해석

**Files:**
- Create: `migrations/core/011_songdb_aliases.sql`
- Modify: `services/chunithm/songdb.py`
- Modify: `core/bot.py`
- Test: `tests/services/chunithm/test_songdb.py`

**Interfaces:**
- Consumes: `SongDbService.__init__`, `SongDbService.resolve`.
- Produces: `SongDbService(conn=...)`(기존 생성자에 키워드 `conn: aiosqlite.Connection | None = None` 추가). `SongDbService.add_alias(*, guild_id, alias, song_title, added_by) -> None`(ValueError on 충돌). `SongDbService.get_alias(*, guild_id, alias) -> AliasRow | None`. `SongDbService.remove_alias(*, guild_id, alias) -> None`. `resolve()`가 `ctx.guild_id`로 서버 별칭을 본다.

- [ ] **Step 1: 마이그레이션 파일 작성**

`migrations/core/011_songdb_aliases.sql`:

```sql
-- migrations/core/011_songdb_aliases.sql
CREATE TABLE songdb_aliases (
    guild_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    song_title TEXT NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, alias)
);
```

- [ ] **Step 2: 실패하는 테스트 — 서버별 별칭 등록/해석/삭제/권한**

`tests/services/chunithm/test_songdb.py` 맨 위 import에 추가:

```python
from collections.abc import AsyncIterator

import aiosqlite
```

새 픽스처와 테스트 추가:

```python
@pytest.fixture
async def songdb_with_conn() -> AsyncIterator[SongDbService]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/011_songdb_aliases.sql").read_text())
    await conn.commit()
    try:
        yield SongDbService(conn=conn)
    finally:
        await conn.close()


async def test_resolve_without_conn_ignores_server_alias(songdb: SongDbService) -> None:
    # conn 없이 만든 서비스는(이 모듈의 대부분 테스트처럼) 서버 별칭 기능이 아예 없다 —
    # 그래도 전역 해석은 그대로 동작해야 한다.
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("크로노", _CTX)


async def test_add_and_resolve_server_alias(songdb_with_conn: SongDbService) -> None:
    ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    await songdb_with_conn.add_alias(guild_id="g1", alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="p1")

    song = await songdb_with_conn.resolve("크로노", ctx)

    assert song.title == "B.B.K.K.B.K.K."


async def test_server_alias_is_scoped_to_guild(songdb_with_conn: SongDbService) -> None:
    await songdb_with_conn.add_alias(guild_id="g1", alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="p1")
    other_guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g2")

    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb_with_conn.resolve("크로노", other_guild_ctx)


async def test_add_alias_already_used_for_different_song_raises(songdb_with_conn: SongDbService) -> None:
    await songdb_with_conn.add_alias(guild_id="g1", alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="p1")

    with pytest.raises(ValueError, match="이미"):
        await songdb_with_conn.add_alias(guild_id="g1", alias="크로노", song_title="Reach For The Stars", added_by="p2")


async def test_get_and_remove_alias(songdb_with_conn: SongDbService) -> None:
    await songdb_with_conn.add_alias(guild_id="g1", alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="p1")

    row = await songdb_with_conn.get_alias(guild_id="g1", alias="크로노")
    assert row is not None
    assert row.added_by == "p1"

    await songdb_with_conn.remove_alias(guild_id="g1", alias="크로노")
    assert await songdb_with_conn.get_alias(guild_id="g1", alias="크로노") is None
```

- [ ] **Step 3: 테스트 실행 → 실패 확인**

Run: `uv run pytest tests/services/chunithm/test_songdb.py -v`
Expected: FAIL — `SongDbService`가 `conn` 키워드도, `add_alias`/`get_alias`/`remove_alias`도 모른다.

- [ ] **Step 4: `services/chunithm/songdb.py` 수정**

`AliasRow` 데이터클래스 추가:

```python
@dataclass(slots=True, kw_only=True)
class AliasRow:
    guild_id: str
    alias: str
    song_title: str
    added_by: str
```

`SongDbService.__init__` 수정:

```python
    def __init__(self, seed_path: Path = _SEED_PATH, *, conn: aiosqlite.Connection | None = None) -> None:
        self._songs = _load_seed(seed_path)
        raw = json.loads(seed_path.read_text(encoding="utf-8"))
        self._available_intl = {e["title"] for e in raw if e.get("available")}
        self._conn = conn
        if self._conn is not None:
            self._conn.row_factory = aiosqlite.Row
```

(파일 맨 위에 `import aiosqlite` 추가.)

별칭 메서드 추가:

```python
    async def add_alias(self, *, guild_id: str, alias: str, song_title: str, added_by: str) -> None:
        assert self._conn is not None
        existing = await self.get_alias(guild_id=guild_id, alias=alias)
        if existing is not None and existing.song_title != song_title:
            raise ValueError(f"'{alias}'는 이미 '{existing.song_title}'에 쓰인 별칭이다")
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO songdb_aliases (guild_id, alias, song_title, added_by, created_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (guild_id, alias) DO UPDATE SET "
            "added_by = excluded.added_by, created_at = excluded.created_at",
            (guild_id, alias, song_title, added_by, now),
        )
        await self._conn.commit()

    async def get_alias(self, *, guild_id: str, alias: str) -> AliasRow | None:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT guild_id, alias, song_title, added_by FROM songdb_aliases WHERE guild_id = ? AND alias = ?",
            (guild_id, alias),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return AliasRow(guild_id=row["guild_id"], alias=row["alias"], song_title=row["song_title"], added_by=row["added_by"])

    async def remove_alias(self, *, guild_id: str, alias: str) -> None:
        assert self._conn is not None
        await self._conn.execute("DELETE FROM songdb_aliases WHERE guild_id = ? AND alias = ?", (guild_id, alias))
        await self._conn.commit()
```

(`from datetime import UTC, datetime` import 추가.)

`resolve()`를 수정해 전역 별칭 검사 전에 서버 별칭도 같은 우선순위로 본다:

```python
    async def resolve(self, raw: str, ctx: ToolContext) -> SongRecord:
        text = raw.strip()
        available_songs = [s for title, s in self._songs.items() if title in self._available_intl]

        for song in available_songs:
            if song.title == text:
                return song

        if self._conn is not None and ctx.guild_id is not None:
            server_alias = await self.get_alias(guild_id=ctx.guild_id, alias=text)
            if server_alias is not None:
                song = self._songs.get(server_alias.song_title)
                if song is not None and song.title in self._available_intl:
                    return song

        for song in available_songs:
            if text.lower() in (a.lower() for a in song.aliases):
                return song

        lowered = text.lower()
        candidates = [
            song.title
            for song in available_songs
            if lowered in song.title.lower() or any(lowered in a.lower() for a in song.aliases)
        ]
        if candidates:
            raise ResolutionError(candidates=candidates[:_MAX_CANDIDATES])
        raise ResolutionError(message="찾지 못함")
```

- [ ] **Step 5: `core/bot.py` 와이어링 수정**

```python
    songdb_service = SongDbService(conn=conn)
```

(기존 `songdb_service = SongDbService()` 줄을 고친다. 이 줄은 `conn = await aiosqlite.connect(...)` 이후에 있으므로 순서는 이미 맞다.)

- [ ] **Step 6: 테스트 재실행 → 통과 확인**

Run: `uv run pytest tests/services/chunithm/ tests/core/test_bot_wiring.py -v`
Expected: PASS.

- [ ] **Step 7: 린트 + 타입 검사 + 전체 테스트**

Run: `uv run pytest -q && uv run ruff check . && uv run basedpyright`
Expected: 통과.

- [ ] **Step 8: 커밋**

```bash
git add migrations/core/011_songdb_aliases.sql services/chunithm/songdb.py core/bot.py tests/services/chunithm/test_songdb.py
git commit -m "services(songdb): 서버별 한국어 별칭 저장과 해석"
```

---

## Task 4: `records` 서비스 — 베스트 점수 조회·마지막 동기화 시각·보더 계산 패스스루

**Files:**
- Modify: `services/chunithm/records.py`
- Test: `tests/services/chunithm/test_records.py`

**Interfaces:**
- Consumes: 기존 `RecordsService`, `services/chunithm/records_calc.py`의 `allowed_attacks`/`remaining_to_target`(이미 존재, 아직 아무도 안 씀).
- Produces: `RecordsService.best_score(player_id, *, song_title, difficulty) -> int | None`. `RecordsService.last_sync_at(player_id) -> str | None`. `RecordsService.allowed_attacks(note_count, target_score) -> Decimal | None`(정적 패스스루). `RecordsService.remaining_to_target(note_count, current_score, target_score) -> tuple[int, Decimal] | None`(정적 패스스루).

- [ ] **Step 1: 실패하는 테스트**

`tests/services/chunithm/test_records.py`에 추가:

```python
async def test_best_score_returns_saved_pb(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)
    fetch = _fixture_fetch()
    await service.save_sync(player.id, fetch, ctx)
    target = fetch.best30[0]

    score = await service.best_score(player.id, song_title=target.song.title, difficulty="MAS")

    assert score == target.score


async def test_best_score_returns_none_when_never_played(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    score = await service.best_score(player.id, song_title="존재하지않는곡", difficulty="MAS")

    assert score is None


async def test_last_sync_at_returns_most_recent(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    assert await service.last_sync_at(player.id) is None

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert await service.last_sync_at(player.id) is not None


async def test_allowed_attacks_and_remaining_to_target_are_exposed_passthroughs(conn) -> None:
    players = PlayersService(conn)
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    assert service.allowed_attacks(3000, 1_007_500) is not None
    assert service.remaining_to_target(3000, 1_000_000, 1_007_500) is not None
    assert service.allowed_attacks(None, 1_007_500) is None
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `uv run pytest tests/services/chunithm/test_records.py -v`
Expected: FAIL — `best_score`/`last_sync_at`/`allowed_attacks`/`remaining_to_target` 메서드가 없다.

- [ ] **Step 3: `services/chunithm/records.py` 수정**

import 줄 수정:

```python
from services.chunithm.records_calc import (
    allowed_attacks,
    assign_session_date,
    average_rating,
    chart_rating,
    remaining_to_target,
)
```

`RecordsService` 클래스에 메서드 추가:

```python
    async def best_score(self, player_id: str, *, song_title: str, difficulty: str) -> int | None:
        cursor = await self._conn.execute(
            "SELECT score FROM records_pb WHERE player_id = ? AND game = ? AND song_title = ? AND difficulty = ?",
            (player_id, _GAME, song_title, difficulty),
        )
        row = await cursor.fetchone()
        return row["score"] if row is not None else None

    async def last_sync_at(self, player_id: str) -> str | None:
        cursor = await self._conn.execute(
            "SELECT synced_at FROM records_sync_log WHERE player_id = ? AND game = ? "
            "ORDER BY synced_at DESC LIMIT 1",
            (player_id, _GAME),
        )
        row = await cursor.fetchone()
        return row["synced_at"] if row is not None else None

    @staticmethod
    def allowed_attacks(note_count: int | None, target_score: int) -> Decimal | None:
        """chunithm-border 스킬이 쓰는 순수 계산 패스스루(services/chunithm/records_calc.py)."""
        return allowed_attacks(note_count, target_score)

    @staticmethod
    def remaining_to_target(
        note_count: int | None, current_score: int, target_score: int
    ) -> tuple[int, Decimal] | None:
        return remaining_to_target(note_count, current_score, target_score)
```

- [ ] **Step 4: 테스트 재실행 → 통과 확인**

Run: `uv run pytest tests/services/chunithm/test_records.py -v`
Expected: PASS.

- [ ] **Step 5: 린트 + 타입 검사**

Run: `uv run ruff check . && uv run basedpyright`
Expected: 통과.

- [ ] **Step 6: 커밋**

```bash
git add services/chunithm/records.py tests/services/chunithm/test_records.py
git commit -m "services(records): 베스트 점수 조회, 마지막 동기화 시각, 보더 계산 패스스루"
```

---

## Task 5: `terms` 서비스 — 개념/기술/판정 용어(뜻·출처) + `explain`/`list_terms`

**Files:**
- Modify: `services/chunithm/terms.py`
- Modify: `services/chunithm/data/terms.yaml`
- Test: `tests/services/chunithm/test_terms.py`

**Interfaces:**
- Consumes: 기존 `TermsService`, `_Entry`, `_load_entries`.
- Produces: `TermsService.explain(raw, ctx) -> TermExplanation | None`. `TermsService.list_terms(category: str | None, ctx) -> list[TermListItem]`(알 수 없는 category면 `ValueError`). `CATEGORY_LABELS: dict[str, str]`(모듈 레벨 공개 상수).

- [ ] **Step 1: `terms.yaml`에 개념/기술/판정 용어 4개 추가**

파일 맨 끝(기존 랭크/난이도/램프 다음)에 추가:

```yaml
# --- 아래 네 항목은 2026-10-11, 5단계 구현 중 임시로 채워 넣은 개념/기술/판정 용어다.
# 커뮤니티에서 실제로 쓰는 표현을 확인한 출처가 없어 뜻 설명은 추정이다 — 사용자가
# 검토해 틀린 점을 고치거나 더 흔히 쓰는 표현으로 바꿔야 한다(chunithm-terms.md
# "미확인 사항" 참고. 랭크/난이도/램프는 이미 확인된 값 용어라 손대지 않았다).
- canonical: "저스티스 크리티컬"
  category: judgment
  aliases: [JC, 저크]
  meaning: 가장 높은 단계의 판정. 만점(AJC)을 받으려면 모든 노트를 이 판정으로 쳐야 한다.
- canonical: "안미츠"
  category: technique
  aliases: [비비기]
  meaning: 타이밍이 조금 다른 두 노트를 동시에 쳐서 저스티스 크리티컬 판정 안에 넣는 기술.
- canonical: "상수"
  category: concept
  aliases: []
  meaning: 채보의 난이도를 수치로 나타낸 값. 레이팅과 보더(허용 어택) 계산에 쓰인다.
- canonical: "오버파워"
  category: concept
  aliases: [오파]
  meaning: 레이팅과는 별개로, 곡마다 낸 점수를 누적해 올리는 보조 지표.
```

- [ ] **Step 2: 실패하는 테스트**

`tests/services/chunithm/test_terms.py`에 추가:

```python
async def test_explain_rank_synthesizes_meaning_from_value(terms: TermsService) -> None:
    explanation = await terms.explain("트플플", _CTX)
    assert explanation is not None
    assert explanation.canonical == "SSS+"
    assert explanation.category_label == "랭크"
    assert explanation.meaning is not None and "1,009,000" in explanation.meaning
    assert explanation.scope == "전역"


async def test_explain_concept_term_from_yaml(terms: TermsService) -> None:
    explanation = await terms.explain("비비기", _CTX)
    assert explanation is not None
    assert explanation.canonical == "안미츠"
    assert explanation.category_label == "기술"
    assert "저스티스 크리티컬" in (explanation.meaning or "")


async def test_explain_unknown_term_returns_none(terms: TermsService) -> None:
    assert await terms.explain("이런용어는없다", _CTX) is None


async def test_list_terms_filters_by_category(terms: TermsService) -> None:
    items = await terms.list_terms("랭크", _CTX)
    assert items
    assert all(item.category_label == "랭크" for item in items)
    assert len(items) == 14  # terms.yaml의 랭크 개수


async def test_list_terms_unknown_category_raises(terms: TermsService) -> None:
    with pytest.raises(ValueError, match="모르는 분류"):
        await terms.list_terms("없는분류", _CTX)


async def test_list_terms_without_category_returns_everything(terms: TermsService) -> None:
    all_items = await terms.list_terms(None, _CTX)
    rank_items = await terms.list_terms("랭크", _CTX)
    assert len(all_items) > len(rank_items)
```

- [ ] **Step 3: 테스트 실행 → 실패 확인**

Run: `uv run pytest tests/services/chunithm/test_terms.py -v`
Expected: FAIL — `explain`/`list_terms`가 없다.

- [ ] **Step 4: `services/chunithm/terms.py` 수정**

`_Entry`에 필드 추가:

```python
@dataclass(slots=True, kw_only=True)
class _Entry:
    canonical: str
    category: str
    value: int | None
    aliases: tuple[str, ...]
    meaning: str | None = None
    source: str | None = None
```

`_load_entries`에서 새 필드 읽기:

```python
def _load_entries(data_path: Path) -> list[_Entry]:
    raw = yaml.safe_load(data_path.read_text(encoding="utf-8")) or []
    return [
        _Entry(
            canonical=item["canonical"],
            category=item["category"],
            value=item.get("value"),
            aliases=tuple(item.get("aliases") or ()),
            meaning=item.get("meaning"),
            source=item.get("source"),
        )
        for item in raw
    ]
```

파일 맨 위, `_HANGUL_SCALE` 옆에 분류 라벨 상수 추가:

```python
CATEGORY_LABELS: dict[str, str] = {
    "rank": "랭크", "difficulty": "난이도", "lamp": "램프",
    "concept": "개념", "technique": "기술", "judgment": "판정",
}
_LABEL_TO_CATEGORY: dict[str, str] = {label: key for key, label in CATEGORY_LABELS.items()}
```

새 데이터클래스 추가:

```python
@dataclass(slots=True, kw_only=True)
class TermExplanation:
    canonical: str
    category_label: str
    aliases: tuple[str, ...]
    value: int | None
    meaning: str | None
    source: str
    scope: str


@dataclass(slots=True, kw_only=True)
class TermListItem:
    canonical: str
    category_label: str
    aliases: tuple[str, ...]
    value: int | None
```

`TermsService.__init__`에 `self._entries = entries` 저장(이미 `entries = _load_entries(data_path)`가 있으므로 그 바로 아래에 한 줄 추가).

메서드 추가:

```python
    async def explain(self, raw: str, ctx: ToolContext) -> TermExplanation | None:
        text = raw.strip().lower()
        for entry in self._entries:
            if entry.canonical.lower() == text or text in (a.lower() for a in entry.aliases):
                meaning = entry.meaning
                if meaning is None and entry.category == "rank" and entry.value is not None:
                    meaning = f"{entry.value:,}점 이상"
                return TermExplanation(
                    canonical=entry.canonical,
                    category_label=CATEGORY_LABELS.get(entry.category, entry.category),
                    aliases=entry.aliases, value=entry.value, meaning=meaning,
                    source=entry.source or "전역 정의", scope="전역",
                )
        return None

    async def list_terms(self, category: str | None, ctx: ToolContext) -> list[TermListItem]:
        key = self._normalize_category(category) if category is not None else None
        return [
            TermListItem(
                canonical=e.canonical, category_label=CATEGORY_LABELS.get(e.category, e.category),
                aliases=e.aliases, value=e.value,
            )
            for e in self._entries
            if key is None or e.category == key
        ]

    def _normalize_category(self, raw: str) -> str:
        text = raw.strip()
        if text in CATEGORY_LABELS:
            return text
        if text in _LABEL_TO_CATEGORY:
            return _LABEL_TO_CATEGORY[text]
        raise ValueError(f"모르는 분류: {text}")
```

(Task 6에서 `explain`/`list_terms`에 서버 은어 조회를 덧붙인다 — 지금은 전역 용어만 본다.)

- [ ] **Step 5: 테스트 재실행 → 통과 확인**

Run: `uv run pytest tests/services/chunithm/test_terms.py -v`
Expected: PASS.

- [ ] **Step 6: 린트 + 타입 검사**

Run: `uv run ruff check . && uv run basedpyright`
Expected: 통과.

- [ ] **Step 7: 커밋**

```bash
git add services/chunithm/terms.py services/chunithm/data/terms.yaml tests/services/chunithm/test_terms.py
git commit -m "services(terms): 개념/기술/판정 용어 설명(explain)과 분류별 목록(list_terms)"
```

---

## Task 6: `terms` 서비스 — 서버 은어 제안/승인(`terms_server` 연동)

**Files:**
- Modify: `services/chunithm/terms.py`
- Modify: `core/bot.py`
- Test: `tests/services/chunithm/test_terms.py`

**Interfaces:**
- Consumes: 기존 `TermsService`, `migrations/core/009_terms_server.sql`(4단계에서 이미 만들어짐).
- Produces: `TermsService(conn=...)`(생성자에 키워드 `conn: aiosqlite.Connection | None = None` 추가). `TermsService.propose(*, guild_id, term, meaning, aliases, suggested_by) -> str`(제안 id). `TermsService.get_proposal(proposal_id) -> ServerTermProposal | None`. `TermsService.review(proposal_id, *, approve, reviewed_by) -> ServerTermProposal | None`. `explain`/`list_terms`가 이제 전역에서 못 찾으면 이 서버의 승인된 은어도 본다.

- [ ] **Step 1: 실패하는 테스트**

`tests/services/chunithm/test_terms.py` 맨 위 import에 추가:

```python
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
```

새 픽스처와 테스트:

```python
_TERMS_SERVER_MIGRATION = Path("migrations/core/009_terms_server.sql").read_text()
_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)


@pytest.fixture
async def terms_with_conn() -> AsyncIterator[TermsService]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION + _TERMS_SERVER_MIGRATION)
    await conn.commit()
    try:
        yield TermsService(conn=conn)
    finally:
        await conn.close()


async def test_propose_and_explain_before_approval_is_not_visible(terms_with_conn: TermsService) -> None:
    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=["테스트별칭"], suggested_by="p1"
    )

    assert await terms_with_conn.explain("테스트은어", guild_ctx) is None


async def test_approved_server_term_is_explained_and_listed(terms_with_conn: TermsService) -> None:
    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=["테스트별칭"], suggested_by="p1"
    )
    await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin1")

    explanation = await terms_with_conn.explain("테스트별칭", guild_ctx)
    assert explanation is not None
    assert explanation.canonical == "테스트은어"
    assert explanation.scope == "이 서버"

    items = await terms_with_conn.list_terms(None, guild_ctx)
    assert any(item.canonical == "테스트은어" for item in items)


async def test_server_term_is_not_visible_in_another_guild(terms_with_conn: TermsService) -> None:
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=[], suggested_by="p1"
    )
    await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin1")

    other_guild_ctx = ToolContext(player_id="p2", tags=frozenset(), services={}, guild_id="g2")
    assert await terms_with_conn.explain("테스트은어", other_guild_ctx) is None


async def test_global_term_wins_over_same_named_server_term(terms_with_conn: TermsService) -> None:
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="SSS+", meaning="서버에서 다르게 부르는 뜻", aliases=[], suggested_by="p1"
    )
    await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin1")

    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    explanation = await terms_with_conn.explain("SSS+", guild_ctx)
    assert explanation is not None
    assert explanation.scope == "전역"  # 전역 랭크가 이긴다


async def test_review_reject_keeps_term_invisible(terms_with_conn: TermsService) -> None:
    guild_ctx = ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1")
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="테스트용 뜻", aliases=[], suggested_by="p1"
    )
    rejected = await terms_with_conn.review(proposal_id, approve=False, reviewed_by="admin1")

    assert rejected is not None and rejected.status == "rejected"
    assert await terms_with_conn.explain("테스트은어", guild_ctx) is None


async def test_review_already_reviewed_proposal_returns_none(terms_with_conn: TermsService) -> None:
    proposal_id = await terms_with_conn.propose(
        guild_id="g1", term="테스트은어", meaning="뜻", aliases=[], suggested_by="p1"
    )
    await terms_with_conn.review(proposal_id, approve=True, reviewed_by="admin1")

    second = await terms_with_conn.review(proposal_id, approve=False, reviewed_by="admin1")
    assert second is None
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `uv run pytest tests/services/chunithm/test_terms.py -v`
Expected: FAIL — `TermsService`가 `conn` 키워드도, `propose`/`get_proposal`/`review`도 모른다.

- [ ] **Step 3: `services/chunithm/terms.py` 수정**

파일 맨 위 import 추가: `import json`, `import uuid`, `import aiosqlite`.

`ServerTermProposal` 데이터클래스 추가:

```python
@dataclass(slots=True, kw_only=True)
class ServerTermProposal:
    id: str
    guild_id: str
    term: str
    meaning: str
    aliases: tuple[str, ...]
    suggested_by: str
    status: str
    reviewed_by: str | None
    created_at: str
    reviewed_at: str | None


def _row_to_proposal(row: aiosqlite.Row) -> ServerTermProposal:
    return ServerTermProposal(
        id=row["id"], guild_id=row["guild_id"], term=row["term"], meaning=row["meaning"],
        aliases=tuple(json.loads(row["aliases"])), suggested_by=row["suggested_by"],
        status=row["status"], reviewed_by=row["reviewed_by"], created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
    )
```

`TermsService.__init__` 수정:

```python
    def __init__(self, data_path: Path = _DATA_PATH, *, conn: aiosqlite.Connection | None = None) -> None:
        entries = _load_entries(data_path)
        self._entries = entries
        self._rank_aliases = _alias_map(entries, "rank")
        self._rank_values = _value_map(entries, "rank")
        self._difficulty_aliases = _alias_map(entries, "difficulty")
        self._lamp_aliases = _alias_map(entries, "lamp")
        self._lamp_values = _value_map(entries, "lamp")
        self._conn = conn
        if self._conn is not None:
            self._conn.row_factory = aiosqlite.Row
```

메서드 추가:

```python
    async def propose(
        self, *, guild_id: str, term: str, meaning: str, aliases: list[str], suggested_by: str
    ) -> str:
        assert self._conn is not None
        proposal_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO terms_server (id, guild_id, term, meaning, aliases, suggested_by, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'suggested', ?)",
            (proposal_id, guild_id, term, meaning, json.dumps(aliases), suggested_by, now),
        )
        await self._conn.commit()
        return proposal_id

    async def get_proposal(self, proposal_id: str) -> ServerTermProposal | None:
        assert self._conn is not None
        cursor = await self._conn.execute("SELECT * FROM terms_server WHERE id = ?", (proposal_id,))
        row = await cursor.fetchone()
        return _row_to_proposal(row) if row is not None else None

    async def review(self, proposal_id: str, *, approve: bool, reviewed_by: str) -> ServerTermProposal | None:
        assert self._conn is not None
        proposal = await self.get_proposal(proposal_id)
        if proposal is None or proposal.status != "suggested":
            return None
        now = datetime.now(UTC).isoformat()
        status = "approved" if approve else "rejected"
        await self._conn.execute(
            "UPDATE terms_server SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
            (status, reviewed_by, now, proposal_id),
        )
        await self._conn.commit()
        return await self.get_proposal(proposal_id)

    async def _approved_server_terms(self, guild_id: str) -> list[ServerTermProposal]:
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT * FROM terms_server WHERE guild_id = ? AND status = 'approved' ORDER BY term", (guild_id,)
        )
        return [_row_to_proposal(row) for row in await cursor.fetchall()]

    async def _find_approved_server_term(self, guild_id: str, text: str) -> ServerTermProposal | None:
        for proposal in await self._approved_server_terms(guild_id):
            if proposal.term.lower() == text or text in (a.lower() for a in proposal.aliases):
                return proposal
        return None
```

(파일 맨 위 `from datetime import UTC, datetime` import 추가 — 아직 없다면.)

`explain`/`list_terms` 수정 — 전역에서 못 찾으면 서버 은어를 본다:

```python
    async def explain(self, raw: str, ctx: ToolContext) -> TermExplanation | None:
        text = raw.strip().lower()
        for entry in self._entries:
            if entry.canonical.lower() == text or text in (a.lower() for a in entry.aliases):
                meaning = entry.meaning
                if meaning is None and entry.category == "rank" and entry.value is not None:
                    meaning = f"{entry.value:,}점 이상"
                return TermExplanation(
                    canonical=entry.canonical,
                    category_label=CATEGORY_LABELS.get(entry.category, entry.category),
                    aliases=entry.aliases, value=entry.value, meaning=meaning,
                    source=entry.source or "전역 정의", scope="전역",
                )
        if self._conn is not None and ctx.guild_id is not None:
            proposal = await self._find_approved_server_term(ctx.guild_id, text)
            if proposal is not None:
                return TermExplanation(
                    canonical=proposal.term, category_label="서버 은어", aliases=proposal.aliases,
                    value=None, meaning=proposal.meaning, source="서버 사용", scope="이 서버",
                )
        return None

    async def list_terms(self, category: str | None, ctx: ToolContext) -> list[TermListItem]:
        key = self._normalize_category(category) if category is not None else None
        items = [
            TermListItem(
                canonical=e.canonical, category_label=CATEGORY_LABELS.get(e.category, e.category),
                aliases=e.aliases, value=e.value,
            )
            for e in self._entries
            if key is None or e.category == key
        ]
        if self._conn is not None and ctx.guild_id is not None and key is None:
            for proposal in await self._approved_server_terms(ctx.guild_id):
                items.append(
                    TermListItem(canonical=proposal.term, category_label="서버 은어", aliases=proposal.aliases, value=None)
                )
        return items
```

- [ ] **Step 4: `core/bot.py` 와이어링 수정**

```python
    terms_service = TermsService(conn=conn)
```

(기존 `terms_service = TermsService()` 줄을 고친다.)

- [ ] **Step 5: 테스트 재실행 → 통과 확인**

Run: `uv run pytest tests/services/chunithm/test_terms.py tests/core/test_bot_wiring.py -v`
Expected: PASS.

- [ ] **Step 6: 전체 테스트 + 린트 + 타입 검사**

Run: `uv run pytest -q && uv run ruff check . && uv run basedpyright`
Expected: 통과.

- [ ] **Step 7: 커밋**

```bash
git add services/chunithm/terms.py core/bot.py tests/services/chunithm/test_terms.py
git commit -m "services(terms): 서버 은어 제안/승인(terms_server)과 explain/list_terms 연동"
```

---

## Task 7: `chunithm-account` 스킬

**Files:**
- Create: `skills/chunithm-account/SKILL.md`
- Create: `skills/chunithm-account/skill.py`
- Create: `skills/chunithm-account/tests/test_chunithm_account.py`

**Interfaces:**
- Consumes: `ctx.chunithm_net`(`ChunithmNetService.status/login/logout`), `ctx.players`(`get_player`, `set_visibility`), `ctx.records`(`last_sync_at`), `ctx.notify`(`dm`).
- Produces: 슬래시 `/계정`, `/기록공개`, `/로그아웃`, `/로그인`(모달).

- [ ] **Step 1: SKILL.md 작성**

```markdown
---
name: chunithm-account
description: CHUNITHM-NET 로그인 상태 확인, 로그인 방법 안내, 기록 공개 설정을 다룬다.
examples:
  - 나 로그인 돼 있어?
  - 내 기록 다른 사람도 볼 수 있게 해 줘
category: 계정 연결
game: chunithm
version: 1
services: [chunithm_net, secrets, players, notify, records]
listens: [chunithm.login.expired]
---

- 로그인 요청에는 `/로그인`을 안내한다. 쿠키 값을 채팅으로 보내지 말라고 함께 말한다.
- 플레이어가 채팅에 쿠키처럼 보이는 값을 붙였으면, 그 값을 다시 말하지 말고 `/로그인`으로 다시 넣어 달라고 한다.
- 공개 설정을 바꾸기 전에 무엇이 남에게 보이게 되는지 한 줄로 알려 준다.
```

- [ ] **Step 2: 실패하는 테스트부터 — status/set_privacy/logout/로그인 모달/만료 핸들러**

`skills/chunithm-account/tests/test_chunithm_account.py`:

```python
# skills/chunithm-account/tests/test_chunithm_account.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from core.tool_types import Display, ToolContext, ToolResult

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_account_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeChunithmNet:
    def __init__(self, *, linked: bool, nickname: str | None = None) -> None:
        from services.chunithm.chunithm_net import LoginStatus

        self._status = LoginStatus(linked=linked, nickname=nickname, last_checked_at="2026-10-11T00:00:00+00:00")
        self.logged_out: list[str] = []

    async def status(self, player_id: str):
        return self._status

    async def logout(self, player_id: str, ctx) -> None:
        self.logged_out.append(player_id)


class _FakePlayers:
    def __init__(self, *, records_public: bool) -> None:
        from dataclasses import dataclass

        @dataclass(slots=True, kw_only=True)
        class _P:
            records_public: bool

        self._player = _P(records_public=records_public)
        self.visibility_calls: list[tuple[str, str, bool]] = []

    async def get_player(self, player_id: str):
        return self._player

    async def set_visibility(self, player_id: str, field: str, value: bool) -> None:
        self.visibility_calls.append((player_id, field, value))
        self._player.records_public = value


class _FakeRecords:
    def __init__(self, last_sync_at: str | None) -> None:
        self._last = last_sync_at

    async def last_sync_at(self, player_id: str):
        return self._last


class _FakeNotify:
    def __init__(self) -> None:
        self.dms: list[tuple[str, str]] = []

    async def dm(self, player_id: str, content: str, *, view=None) -> None:
        self.dms.append((player_id, content))


def _ctx(*, chunithm_net=None, players=None, records=None, notify=None) -> ToolContext:
    services: dict[str, object] = {}
    if chunithm_net is not None:
        services["chunithm_net"] = chunithm_net
    if players is not None:
        services["players"] = players
    if records is not None:
        services["records"] = records
    if notify is not None:
        services["notify"] = notify
    return ToolContext(player_id="p1", tags=frozenset(), services=services)


async def test_status_reports_linked_and_last_sync() -> None:
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=True, nickname="유니상")
    players = _FakePlayers(records_public=False)
    records = _FakeRecords("2026-10-11T03:00:00+00:00")

    result = await skill.status(_ctx(chunithm_net=chunithm_net, players=players, records=records))

    assert result.error is None
    assert result.data["linked"] is True
    assert result.data["nickname"] == "유니상"
    assert result.data["last_synced_at"] == "2026-10-11T03:00:00+00:00"
    assert result.data["records_public"] is False


async def test_status_reports_never_synced() -> None:
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=False)
    players = _FakePlayers(records_public=False)
    records = _FakeRecords(None)

    result = await skill.status(_ctx(chunithm_net=chunithm_net, players=players, records=records))

    assert result.data["last_synced_at"] is None
    assert result.display is not None and "아직" in (result.display.content or "")


async def test_set_privacy_updates_visibility() -> None:
    skill = _load_skill()
    players = _FakePlayers(records_public=False)

    result = await skill.set_privacy(_ctx(players=players), public=True)

    assert result.error is None
    assert players.visibility_calls == [("p1", "records_public", True)]


async def test_logout_clears_cookie() -> None:
    skill = _load_skill()
    chunithm_net = _FakeChunithmNet(linked=True)

    result = await skill.logout(_ctx(chunithm_net=chunithm_net))

    assert result.error is None
    assert chunithm_net.logged_out == ["p1"]


async def test_login_modal_success_sends_followup() -> None:
    skill = _load_skill()

    class _LoginSuccessChunithmNet:
        async def login(self, player_id: str, clal: str, ctx) -> object:
            from types import SimpleNamespace

            assert clal == "clal=abc123"
            return SimpleNamespace(username="유니상")

    class _FakeResponse:
        async def defer(self, *, ephemeral: bool = False) -> None:
            pass

    class _FakeFollowup:
        def __init__(self) -> None:
            self.sent: list[tuple[str, bool]] = []

        async def send(self, content: str, *, ephemeral: bool = False) -> None:
            self.sent.append((content, ephemeral))

    class _FakeInteraction:
        def __init__(self) -> None:
            self.response = _FakeResponse()
            self.followup = _FakeFollowup()

    modal = skill._LoginModal(ctx=_ctx(chunithm_net=_LoginSuccessChunithmNet()))
    modal.clal_input._value = "clal=abc123"  # TextInput의 .value는 제출 후 채워지는 속성
    interaction = _FakeInteraction()

    await modal.on_submit(interaction)

    assert interaction.followup.sent
    assert "유니상" in interaction.followup.sent[0][0]


async def test_login_expired_handler_sends_dm_once() -> None:
    skill = _load_skill()
    notify = _FakeNotify()
    ctx = _ctx(notify=notify)

    await skill.on_login_expired(ctx, {"player_id": "p1"})
    await skill.on_login_expired(ctx, {"player_id": "p1"})

    assert len(notify.dms) == 1
    assert notify.dms[0][0] == "p1"
```

- [ ] **Step 3: 테스트 실행 → 실패 확인(모듈이 없으므로)**

Run: `uv run pytest skills/chunithm-account/tests/test_chunithm_account.py -v`
Expected: FAIL — `skill.py`가 아직 없다.

- [ ] **Step 4: `skills/chunithm-account/skill.py` 작성**

```python
# skills/chunithm-account/skill.py
from __future__ import annotations

import discord

from core.decorators import on, slash_only, tool
from core.tool_types import Display, ToolContext, ToolResult

_notified_expiries: set[str] = set()


class _LoginModal(discord.ui.Modal, title="CHUNITHM-NET 로그인"):
    clal_input: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
        label="clal 쿠키 값",
        style=discord.TextStyle.paragraph,
        placeholder="chunithm-net-eng.com에 로그인한 뒤 개발자 도구에서 복사한 clal=... 값",
        required=True,
        max_length=4000,
    )

    def __init__(self, *, ctx: ToolContext) -> None:
        super().__init__()
        self._ctx = ctx

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # defer 뒤 followup으로만 답한다 — 입력값은 채널에 남지 않고, 여기서도 다시 보여주지 않는다.
        await interaction.response.defer(ephemeral=True)
        try:
            profile = await self._ctx.chunithm_net.login(
                self._ctx.player_id, str(self.clal_input.value), self._ctx
            )
        except Exception:
            await interaction.followup.send(
                "로그인에 실패했다. clal 값이 올바른지, 만료되지 않았는지 확인해 달라.", ephemeral=True
            )
            return
        await interaction.followup.send(f"로그인 완료! ({profile.username})", ephemeral=True)


@slash_only("로그인")
async def start_login(ctx: ToolContext) -> discord.ui.Modal:
    """clal 쿠키를 입력하는 창을 연다. 입력값은 채널에 남지 않는다."""
    return _LoginModal(ctx=ctx)


@tool(slash="계정")
async def status(ctx: ToolContext) -> ToolResult:
    """로그인 여부, CHUNITHM-NET 닉네임, 마지막 동기화 시각, 기록 공개 설정을 보여준다."""
    login_status = await ctx.chunithm_net.status(ctx.player_id)
    player = await ctx.players.get_player(ctx.player_id)
    assert player is not None
    last_synced_at = await ctx.records.last_sync_at(ctx.player_id)

    data = {
        "linked": login_status.linked,
        "nickname": login_status.nickname,
        "last_synced_at": last_synced_at,
        "records_public": player.records_public,
    }
    lines = [
        f"로그인: {'되어있음' if login_status.linked else '안 되어있음'}",
        f"닉네임: {login_status.nickname or '없음'}",
        f"마지막 동기화: {last_synced_at or '아직 동기화한 적 없음'}",
        f"기록 공개: {'공개' if player.records_public else '비공개'}",
    ]
    return ToolResult(data=data, display=Display(content="\n".join(lines)), display_mode="optional")


@tool(slash="기록공개", tags=["chunithm-linked"], mutates=True)
async def set_privacy(ctx: ToolContext, public: bool) -> ToolResult:
    """기록(프로필, 베스트, 곡별 기록) 공개 여부를 켜거나 끈다."""
    await ctx.players.set_visibility(ctx.player_id, "records_public", public)
    message = "이제 다른 사람도 내 기록을 볼 수 있다." if public else "내 기록은 이제 나만 볼 수 있다."
    return ToolResult(
        data={"records_public": public}, display=Display(content=message, ephemeral=True), display_mode="optional"
    )


@tool(slash="로그아웃", tags=["chunithm-linked"], mutates=True, confirm="always")
async def logout(ctx: ToolContext) -> ToolResult:
    """저장된 clal 쿠키를 지운다. 동기화된 기록은 남는다."""
    await ctx.chunithm_net.logout(ctx.player_id, ctx)
    return ToolResult(data={"logged_out": True}, display=Display(content="로그아웃했다.", ephemeral=True))


@on("chunithm.login.expired")
async def on_login_expired(ctx: ToolContext, payload: dict[str, object]) -> None:
    """만료된 로그인에 대해 대표 계정에 재로그인을 한 번만 안내한다(이 프로세스가 켜져
    있는 동안만 — 재시작하면 다시 보낸다. core/bot.py가 이 이벤트를 실제로 ctx와 함께
    호출하도록 배선하는 일은 7단계 chunithm-sync에서 마저 한다 — 결정 사항 2번 참고)."""
    player_id = str(payload["player_id"])
    if player_id in _notified_expiries:
        return
    _notified_expiries.add(player_id)
    await ctx.notify.dm(player_id, "로그인이 풀렸다. `/로그인`으로 다시 넣어 달라.")
```

- [ ] **Step 5: 테스트 재실행 → 통과 확인**

Run: `uv run pytest skills/chunithm-account/tests/test_chunithm_account.py -v`
Expected: PASS. (`TextInput.value`를 테스트에서 직접 쓰기 위해 `_value`를 채우는 방식이 discord.py 버전에 따라 안 통하면, 대신 `monkeypatch.setattr(type(modal.clal_input), "value", property(lambda self: "clal=abc123"))`로 바꾼다 — 구현 세션이 실제로 돌려보고 안 되면 이 대안을 쓴다.)

- [ ] **Step 6: 계약 테스트 포함 전체 테스트 + 린트 + 타입 검사**

Run: `uv run pytest -q && uv run ruff check . && uv run basedpyright`
Expected: 통과(`tests/core/test_contract.py`가 이 스킬도 검사 대상에 들어간다 — manifest/도구 스키마/이름 중복/docstring 등).

- [ ] **Step 7: 커밋**

```bash
git add skills/chunithm-account/
git commit -m "skills(chunithm-account): 로그인 입력창, 계정 상태, 기록 공개, 로그아웃"
```

---

## Task 8: `chunithm-song` 스킬

**Files:**
- Create: `skills/chunithm-song/SKILL.md`
- Create: `skills/chunithm-song/skill.py`
- Create: `skills/chunithm-song/tests/test_chunithm_song.py`

**Interfaces:**
- Consumes: `ctx.songdb`(`resolve`는 코어가 `SongQuery`/`ChartRef`로 미리 해석해 주므로 직접 안 부른다. `find_by_const`, `chart_link`, `add_alias`, `get_alias`, `remove_alias`). `ctx.event_bus.emit_gather`.
- Produces: 슬래시 `/곡`, `/상수`, `/별칭 추가`, `/별칭 삭제`. (`chart`, `chart_link` 도구는 슬래시 없음 — 유닛 테스트로 직접 호출해 확인한다.)

- [ ] **Step 1: SKILL.md 작성**

```markdown
---
name: chunithm-song
description: 국제판 츄니즘 곡과 채보를 찾고 상수, 노트 수, 수록 버전, 채보 뷰어 링크를 보여 준다. 한국어 별칭도 등록한다.
examples:
  - 크로노 마스 상수 얼마야?
  - 15.2에서 15.4 사이 마스 곡 보여 줘
  - 라미아 채보 링크 줘
category: 곡 정보
game: chunithm
version: 1
services: [songdb]
emits: [chunithm.song.viewed]
---

- 상수, 노트 수, 레벨은 도구 결과로만 말한다. 기억으로 말하지 않는다.
- "상수 얼마야?"처럼 값 하나만 물으면 `chart`로 말로 답하고, 곡을 통째로 물으면 `info`를 쓴다.
- 추정 상수면 추정이라고 함께 말한다.
- 후보가 여러 개면 두세 개를 보여 주고 고르게 한다.
- 별칭 추가 요청이 모호하면 어느 곡인지 확인한 뒤 추가한다.
```

- [ ] **Step 2: 실패하는 테스트**

`skills/chunithm-song/tests/test_chunithm_song.py`:

```python
# skills/chunithm-song/tests/test_chunithm_song.py
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.songdb import SongDbService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_CTX_NO_GUILD = ToolContext(player_id="p1", tags=frozenset(), services={}, event_bus=EventBus())


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_song_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ctx_with_songdb(songdb: SongDbService, *, guild_id: str | None = None) -> ToolContext:
    return ToolContext(
        player_id="p1", tags=frozenset(), services={"songdb": songdb}, event_bus=EventBus(), guild_id=guild_id
    )


async def test_info_lists_available_charts_and_emits_viewed_event() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)

    result = await skill.info(_ctx_with_songdb(songdb), song=song)

    assert result.error is None
    assert result.data["title"] == "B.B.K.K.B.K.K."
    assert result.display is not None and "B.B.K.K.B.K.K." in (result.display.content or "")


async def test_chart_reports_const_and_note_count() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)
    chart = song.charts["MAS"]

    from services.chunithm.chart_ref import ResolvedChart

    result = await skill.chart(_ctx_with_songdb(songdb), chart=ResolvedChart(song=song, chart=chart))

    assert result.data["const"] == chart.const
    assert result.data["note_count"] == chart.note_count


async def test_find_by_const_returns_sorted_results() -> None:
    skill = _load_skill()
    songdb = SongDbService()

    result = await skill.find_by_const(
        _ctx_with_songdb(songdb), min_const=12.0, max_const=13.0,
        difficulty=_resolved_difficulty("MASTER"),
    )

    assert result.error is None
    assert result.data["count"] >= 1


async def test_chart_link_prefers_sdvxin_mapping() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)
    chart = song.charts["MAS"]

    from services.chunithm.chart_ref import ResolvedChart

    result = await skill.chart_link(_ctx_with_songdb(songdb), chart=ResolvedChart(song=song, chart=chart))

    assert result.data["has_mapping"] is True
    assert "sdvx.in" in result.data["url"]


async def test_alias_add_requires_guild() -> None:
    skill = _load_skill()
    songdb = SongDbService()
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)

    result = await skill.alias_add(_ctx_with_songdb(songdb, guild_id=None), song=song, alias="크로노")

    assert result.error is not None


async def test_alias_add_then_resolve_by_alias(tmp_path) -> None:
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/011_songdb_aliases.sql").read_text())
    await conn.commit()
    songdb = SongDbService(conn=conn)
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX_NO_GUILD)
    skill = _load_skill()

    result = await skill.alias_add(_ctx_with_songdb(songdb, guild_id="g1"), song=song, alias="크로노")
    assert result.error is None

    resolved_again = await songdb.resolve("크로노", ToolContext(player_id="p1", tags=frozenset(), services={}, guild_id="g1"))
    assert resolved_again.title == "B.B.K.K.B.K.K."
    await conn.close()


async def test_alias_remove_rejects_non_owner_non_admin() -> None:
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/011_songdb_aliases.sql").read_text())
    await conn.commit()
    songdb = SongDbService(conn=conn)
    await songdb.add_alias(guild_id="g1", alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="owner")
    skill = _load_skill()

    ctx = ToolContext(player_id="someone-else", tags=frozenset(), services={"songdb": songdb}, guild_id="g1")
    result = await skill.alias_remove(ctx, alias="크로노")

    assert result.error is not None
    assert await songdb.get_alias(guild_id="g1", alias="크로노") is not None
    await conn.close()


async def test_alias_remove_allows_admin() -> None:
    import aiosqlite

    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(Path("migrations/core/011_songdb_aliases.sql").read_text())
    await conn.commit()
    songdb = SongDbService(conn=conn)
    await songdb.add_alias(guild_id="g1", alias="크로노", song_title="B.B.K.K.B.K.K.", added_by="owner")
    skill = _load_skill()

    ctx = ToolContext(player_id="admin1", tags=frozenset({"admin"}), services={"songdb": songdb}, guild_id="g1")
    result = await skill.alias_remove(ctx, alias="크로노")

    assert result.error is None
    assert await songdb.get_alias(guild_id="g1", alias="크로노") is None
    await conn.close()


def _resolved_difficulty(canonical: str):
    from services.chunithm.terms import ResolvedDifficulty

    return ResolvedDifficulty(canonical=canonical)
```

- [ ] **Step 3: 테스트 실행 → 실패 확인**

Run: `uv run pytest skills/chunithm-song/tests/test_chunithm_song.py -v`
Expected: FAIL — `skill.py`가 없다.

- [ ] **Step 4: `skills/chunithm-song/skill.py` 작성**

```python
# skills/chunithm-song/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import ChartRef, Display, DifficultyRef, SongQuery, ToolContext, ToolResult
from services.chunithm.songdb import LONG_TO_SHORT_DIFFICULTY

_DISPLAY_ORDER = ("BAS", "ADV", "EXP", "MAS", "ULT", "WE")
_MAX_LISTED = 25


@tool(slash="곡")
async def info(ctx: ToolContext, song: SongQuery) -> ToolResult:
    """곡 정보와 난이도별 채보(상수, 노트 수)를 보여준다."""
    lines = [f"{song.title} — {song.artist}".rstrip(" —")]
    if song.bpm is not None:
        lines.append(f"BPM {song.bpm}")
    if song.version is not None:
        lines.append(f"수록 버전: {song.version}")
    for code in _DISPLAY_ORDER:
        chart = song.charts.get(code)
        if chart is None or not chart.is_available:
            continue
        const_suffix = "(추정)" if chart.is_const_unknown else ""
        note_text = f"{chart.note_count}노트" if chart.note_count is not None else "노트 수 알 수 없음"
        lines.append(f"{code} Lv.{chart.level or '?'} 상수 {chart.const}{const_suffix} — {note_text}")

    await ctx.event_bus.emit_gather(
        "chunithm.song.viewed", {"player_id": ctx.player_id, "song_title": song.title, "difficulty": None}
    )

    return ToolResult(
        data={"title": song.title, "artist": song.artist, "is_new": song.is_new},
        display=Display(content="\n".join(lines)),
    )


@tool()
async def chart(ctx: ToolContext, chart: ChartRef) -> ToolResult:
    """채보 하나의 상수와 노트 수를 돌려준다."""
    note_text = f"{chart.chart.note_count}노트" if chart.chart.note_count is not None else "계산 불가"
    return ToolResult(
        data={
            "song": chart.song.title, "difficulty": chart.chart.difficulty,
            "const": chart.chart.const, "note_count": chart.chart.note_count,
            "is_const_unknown": chart.chart.is_const_unknown,
        },
        display=Display(content=f"{chart.song.title} {chart.chart.difficulty} 상수 {chart.chart.const} ({note_text})"),
        display_mode="optional",
    )


@tool(slash="상수")
async def find_by_const(
    ctx: ToolContext, min_const: float, max_const: float, difficulty: DifficultyRef
) -> ToolResult:
    """상수 범위와 난이도로 채보 목록을 상수순으로 보여준다."""
    short_code = LONG_TO_SHORT_DIFFICULTY.get(difficulty.canonical, difficulty.canonical)
    hits = ctx.songdb.find_by_const(min_const=min_const, max_const=max_const, difficulty=short_code)

    lines = [f"{hit.song.title} — 상수 {hit.chart.const}" for hit in hits[:_MAX_LISTED]]
    if len(hits) > _MAX_LISTED:
        lines.append(f"...외 {len(hits) - _MAX_LISTED}개")

    return ToolResult(
        data={"count": len(hits), "titles": [hit.song.title for hit in hits[:_MAX_LISTED]]},
        display=Display(content="\n".join(lines) if lines else "해당 범위의 채보가 없다."),
    )


@tool()
async def chart_link(ctx: ToolContext, chart: ChartRef) -> ToolResult:
    """채보 뷰어 링크(sdvx.in, 없으면 유튜브 검색)를 돌려준다."""
    link = ctx.songdb.chart_link(chart.song, chart.chart)
    return ToolResult(
        data={"url": link.url, "has_mapping": link.has_mapping},
        display=Display(content=link.url),
        display_mode="optional",
    )


@tool(slash="별칭 추가", mutates=True)
async def alias_add(ctx: ToolContext, song: SongQuery, alias: str) -> ToolResult:
    """한국어 별칭을 이 서버에 등록한다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 별칭을 추가할 수 있다")
    try:
        await ctx.songdb.add_alias(guild_id=ctx.guild_id, alias=alias, song_title=song.title, added_by=ctx.player_id)
    except ValueError as exc:
        return ToolResult(error=str(exc))
    return ToolResult(
        data={"alias": alias, "song": song.title},
        display=Display(content=f"'{alias}' → {song.title}로 등록했다."),
        display_mode="optional",
    )


@tool(slash="별칭 삭제", mutates=True)
async def alias_remove(ctx: ToolContext, alias: str) -> ToolResult:
    """별칭을 지운다. 추가한 사람이나 admin만 할 수 있다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 별칭을 지울 수 있다")
    row = await ctx.songdb.get_alias(guild_id=ctx.guild_id, alias=alias)
    if row is None:
        return ToolResult(error="그런 별칭이 없다")
    if row.added_by != ctx.player_id and "admin" not in ctx.tags:
        return ToolResult(error="추가한 사람이나 admin만 지울 수 있다")
    await ctx.songdb.remove_alias(guild_id=ctx.guild_id, alias=alias)
    return ToolResult(data={"alias": alias}, display=Display(content=f"'{alias}' 별칭을 지웠다."), display_mode="optional")
```

(`info`의 함수 인자 이름 `song`과 `chart` 도구의 인자 이름 `chart`가 타입 이름과 겹치는 것은 기존 코드베이스 관례와 같다 — `chart_ref.py`의 `ResolvedChart`처럼 타입 자체를 변수명으로도 쓰는 패턴이 이미 쓰인다. 다만 `chart` 함수 안에서 `chart: ChartRef` 매개변수가 함수 이름 `chart`를 가리므로, 함수 본문에서 `chart.song`/`chart.chart`로 쓰는 것은 매개변수를 가리키는 것이지 재귀 호출이 아니다 — 문제 없다.)

- [ ] **Step 5: 테스트 재실행 → 통과 확인**

Run: `uv run pytest skills/chunithm-song/tests/test_chunithm_song.py -v`
Expected: PASS.

- [ ] **Step 6: 전체 테스트 + 린트 + 타입 검사**

Run: `uv run pytest -q && uv run ruff check . && uv run basedpyright`
Expected: 통과.

- [ ] **Step 7: 커밋**

```bash
git add skills/chunithm-song/
git commit -m "skills(chunithm-song): 곡 정보, 상수 범위 검색, 채보 링크, 서버별 한국어 별칭"
```

---

## Task 9: `chunithm-border` 스킬

**Files:**
- Create: `skills/chunithm-border/SKILL.md`
- Create: `skills/chunithm-border/skill.py`
- Create: `skills/chunithm-border/tests/test_chunithm_border.py`

**Interfaces:**
- Consumes: `ctx.records`(`allowed_attacks`, `remaining_to_target`, `best_score`).
- Produces: 슬래시 `/보더`, `/남은어택`.

- [ ] **Step 1: SKILL.md 작성**

```markdown
---
name: chunithm-border
description: 곡과 난이도별 랭크 보더(허용 어택 개수)와, 내 기록에서 목표 랭크까지 남은 어택을 계산한다.
examples:
  - 크로노 마스 SSS+ 보더 알려줘
  - 내 최고점에서 트플플까지 몇 어택 남았어?
category: 곡 정보
game: chunithm
version: 1
services: [songdb, records]
---

- 보더는 저스티스 0, 미스 0 기준의 허용 어택 개수만 말한다.
- 랭크 하나만 물으면 말로 답하고 표시물은 붙이지 않는다. 여러 랭크를 묻거나 "보더 보여 줘"라고 하면 붙인다.
- 표시물을 붙이면 숫자를 다시 나열하지 말고, 눈에 띄는 점 하나만 짚는다.
- 남은 어택을 말할 때는 남은 점수도 함께 말한다.
- 노트 수가 없는 채보는 계산 불가라고 말한다.
```

- [ ] **Step 2: 실패하는 테스트**

`skills/chunithm-border/tests/test_chunithm_border.py`:

```python
# skills/chunithm-border/tests/test_chunithm_border.py
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from core.tool_types import ToolContext
from services.chunithm.chart_ref import ResolvedChart
from services.chunithm.records import RecordsService
from services.chunithm.songdb import ChartRecord, SongRecord
from services.chunithm.terms import ResolvedRank

_SKILL_PY = Path(__file__).parent.parent / "skill.py"


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_border_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chart(note_count: int | None) -> ResolvedChart:
    song = SongRecord(title="테스트곡", aliases=(), artist="테스터")
    return ResolvedChart(song=song, chart=ChartRecord(difficulty="MAS", const=13.0, note_count=note_count))


class _FakeRecords:
    def __init__(self, *, best_score: int | None) -> None:
        self._best_score = best_score

    @staticmethod
    def allowed_attacks(note_count, target_score):
        from services.chunithm.records_calc import allowed_attacks

        return allowed_attacks(note_count, target_score)

    @staticmethod
    def remaining_to_target(note_count, current_score, target_score):
        from services.chunithm.records_calc import remaining_to_target

        return remaining_to_target(note_count, current_score, target_score)

    async def best_score(self, player_id: str, *, song_title: str, difficulty: str):
        return self._best_score


def _ctx(records: _FakeRecords) -> ToolContext:
    return ToolContext(player_id="p1", tags=frozenset(), services={"records": records})


async def test_lookup_without_rank_shows_all_four_borders() -> None:
    skill = _load_skill()
    result = await skill.lookup(_ctx(_FakeRecords(best_score=None)), chart=_chart(3000), rank=None)

    assert result.error is None
    assert set(result.data["borders"]) == {"SSS+", "SSS", "SS+", "SS"}
    assert result.data["borders"]["SSS"] == "14.70"
    assert result.data["borders"]["SSS+"] == "5.88"


async def test_lookup_with_specific_rank() -> None:
    skill = _load_skill()
    result = await skill.lookup(
        _ctx(_FakeRecords(best_score=None)), chart=_chart(3000), rank=ResolvedRank(canonical="SSS+", min_score=1_009_000)
    )

    assert result.data["borders"] == {"SSS+": "5.88"}


async def test_lookup_unknown_note_count_is_not_computable() -> None:
    skill = _load_skill()
    result = await skill.lookup(_ctx(_FakeRecords(best_score=None)), chart=_chart(None), rank=None)

    assert all(v == "계산 불가" for v in result.data["borders"].values())


async def test_remaining_reports_score_and_attacks() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=1_000_000)), chart=_chart(3000),
        rank=ResolvedRank(canonical="SSS", min_score=1_007_500),
    )

    assert result.error is None
    assert result.data["remaining_score"] == 7_500
    assert Decimal(result.data["remaining_attacks"]) > 0


async def test_remaining_when_already_past_target() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=1_008_000)), chart=_chart(3000),
        rank=ResolvedRank(canonical="SS", min_score=1_000_000),
    )

    assert result.error is None
    assert result.data["already_achieved"] is True
    assert result.data["remaining_attacks"] == "0.00"


async def test_remaining_without_any_play_record_is_an_error() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=None)), chart=_chart(3000),
        rank=ResolvedRank(canonical="SSS", min_score=1_007_500),
    )

    assert result.error is not None
    assert "기록" in result.error


async def test_remaining_unknown_note_count_is_an_error() -> None:
    skill = _load_skill()
    result = await skill.remaining(
        _ctx(_FakeRecords(best_score=1_000_000)), chart=_chart(None),
        rank=ResolvedRank(canonical="SSS", min_score=1_007_500),
    )

    assert result.error is not None
    assert "계산 불가" in result.error
```

- [ ] **Step 3: 테스트 실행 → 실패 확인**

Run: `uv run pytest skills/chunithm-border/tests/test_chunithm_border.py -v`
Expected: FAIL — `skill.py`가 없다.

- [ ] **Step 4: `skills/chunithm-border/skill.py` 작성**

```python
# skills/chunithm-border/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import ChartRef, Display, RankRef, ToolContext, ToolResult

# chunithm-border.md "계산" 표가 명시한 기준 점수 — terms 서비스를 또 부르지 않고
# 설계서가 고정값으로 제시한 숫자를 그대로 쓴다(결정 사항 8번).
_DEFAULT_BORDER_RANKS: tuple[tuple[str, int], ...] = (
    ("SSS+", 1_009_000), ("SSS", 1_007_500), ("SS+", 1_005_000), ("SS", 1_000_000),
)


@tool(slash="보더")
async def lookup(ctx: ToolContext, chart: ChartRef, rank: RankRef | None = None) -> ToolResult:
    """곡과 난이도의 랭크별 허용 어택 개수를 계산한다. 랭크를 안 주면 SSS+/SSS/SS+/SS 네 개를 모두 보여준다."""
    targets = [(rank.canonical, rank.min_score)] if rank is not None else list(_DEFAULT_BORDER_RANKS)

    borders: dict[str, str] = {}
    for name, min_score in targets:
        attacks = ctx.records.allowed_attacks(chart.chart.note_count, min_score)
        borders[name] = "계산 불가" if attacks is None else str(attacks)

    display_lines = [f"{chart.song.title} {chart.chart.difficulty} 보더"]
    display_lines += [f"{name}: {value}어택" for name, value in borders.items()]

    return ToolResult(
        data={"song": chart.song.title, "difficulty": chart.chart.difficulty, "borders": borders},
        display=Display(content="\n".join(display_lines)),
        display_mode="optional",
    )


@tool(slash="남은어택", tags=["chunithm-synced"])
async def remaining(ctx: ToolContext, chart: ChartRef, rank: RankRef) -> ToolResult:
    """내 최고 점수에서 목표 랭크까지 남은 점수와 어택을 계산한다."""
    best_score = await ctx.records.best_score(
        ctx.player_id, song_title=chart.song.title, difficulty=chart.chart.difficulty
    )
    if best_score is None:
        return ToolResult(error="그 채보를 아직 플레이한 기록이 없다")

    result = ctx.records.remaining_to_target(chart.chart.note_count, best_score, rank.min_score)
    if result is None:
        return ToolResult(error="노트 수를 몰라 계산 불가다")

    remaining_score, attacks = result
    already_achieved = remaining_score <= 0

    data = {
        "song": chart.song.title, "difficulty": chart.chart.difficulty, "rank": rank.canonical,
        "best_score": best_score, "remaining_score": remaining_score,
        "remaining_attacks": str(attacks), "already_achieved": already_achieved,
    }
    if already_achieved:
        content = f"{chart.song.title} {chart.chart.difficulty} — 이미 {rank.canonical}를 달성했다."
    else:
        content = f"{chart.song.title} {chart.chart.difficulty} — {rank.canonical}까지 {remaining_score}점 ({attacks}어택)."

    return ToolResult(data=data, display=Display(content=content), display_mode="optional")
```

- [ ] **Step 5: 테스트 재실행 → 통과 확인**

Run: `uv run pytest skills/chunithm-border/tests/test_chunithm_border.py -v`
Expected: PASS.

- [ ] **Step 6: 전체 테스트 + 린트 + 타입 검사**

Run: `uv run pytest -q && uv run ruff check . && uv run basedpyright`
Expected: 통과.

- [ ] **Step 7: 커밋**

```bash
git add skills/chunithm-border/
git commit -m "skills(chunithm-border): 랭크 보더와 남은 어택 계산"
```

---

## Task 10: `chunithm-terms` 스킬

**Files:**
- Create: `skills/chunithm-terms/SKILL.md`
- Create: `skills/chunithm-terms/skill.py`
- Create: `skills/chunithm-terms/tests/test_chunithm_terms.py`

**Interfaces:**
- Consumes: `ctx.terms`(`explain`, `list_terms`, `propose`, `review`).
- Produces: 슬래시 `/용어 뜻`, `/용어 목록`, `/용어 제안`, `/용어 승인`.

- [ ] **Step 1: SKILL.md 작성**

```markdown
---
name: chunithm-terms
description: 츄니즘 용어와 은어(트플플, 이론치, 오버파워, 비비기 등)의 뜻을 설명하고, 서버에서 쓰는 은어를 등록한다.
examples:
  - 오버파워가 뭐야?
  - 트플플가 몇 점부터야?
  - 비비기가 뭐야?
category: 질문 답변
game: chunithm
version: 1
services: [terms, players, notify]
---

- 용어 뜻은 사전에 있는 내용만 말한다. 사전에 없으면 `web-qa`를 펼친다.
- 랭크·램프의 점수 기준은 도구 결과의 값만 쓴다.
- 서버 은어를 설명할 때는 이 서버에서 쓰는 말이라고 밝힌다.
- 플레이어가 모르는 은어를 써서 해석에 실패하면, 뜻을 물어보고 용어 제안을 권할 수 있다.
```

- [ ] **Step 2: 실패하는 테스트**

`skills/chunithm-terms/tests/test_chunithm_terms.py`:

```python
# skills/chunithm-terms/tests/test_chunithm_terms.py
from __future__ import annotations

import importlib.util
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType

import aiosqlite
import pytest

from core.tool_types import ToolContext
from services.chunithm.terms import TermsService

_SKILL_PY = Path(__file__).parent.parent / "skill.py"
_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
_TERMS_SERVER_MIGRATION = Path("migrations/core/009_terms_server.sql").read_text()


def _load_skill() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_chunithm_terms_skill_under_test", _SKILL_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def terms() -> AsyncIterator[TermsService]:
    conn = await aiosqlite.connect(":memory:")
    await conn.executescript(_PLAYERS_MIGRATION + _TERMS_SERVER_MIGRATION)
    await conn.commit()
    try:
        yield TermsService(conn=conn)
    finally:
        await conn.close()


def _ctx(terms_service: TermsService, *, player_id: str = "p1", tags: frozenset[str] = frozenset(), guild_id: str | None = "g1") -> ToolContext:
    return ToolContext(player_id=player_id, tags=tags, services={"terms": terms_service}, guild_id=guild_id)


async def test_explain_known_term(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.explain(_ctx(terms), term="트플플")

    assert result.error is None
    assert result.data["canonical"] == "SSS+"


async def test_explain_unknown_term_is_an_error(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.explain(_ctx(terms), term="아무말")

    assert result.error is not None


async def test_list_known_category(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.list(_ctx(terms), category="랭크")

    assert result.error is None
    assert result.data["count"] == 14


async def test_list_unknown_category_is_an_error(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.list(_ctx(terms), category="없는분류")

    assert result.error is not None


async def test_suggest_creates_a_pending_proposal(terms: TermsService) -> None:
    skill = _load_skill()
    result = await skill.suggest(_ctx(terms), term="테스트은어", meaning="테스트 뜻", aliases="별칭1, 별칭2")

    assert result.error is None
    proposal_id = result.data["proposal_id"]
    proposal = await terms.get_proposal(proposal_id)
    assert proposal is not None and proposal.status == "suggested"
    assert proposal.aliases == ("별칭1", "별칭2")


async def test_review_requires_admin_tag_via_executor(terms: TermsService) -> None:
    """review 함수 자체는 태그를 모른다(실행기가 막는다) — 그래서 이 테스트는
    core.executor.ToolExecutor를 통해 호출해야 admin 태그 검사가 실제로 걸리는지 확인할 수 있다."""
    from core.decorators import ToolSpec
    from core.executor import ToolExecutor
    from core.permissions import InMemoryTagProvider
    from core.resolvers import ResolverRegistry

    skill = _load_skill()
    suggestion = await skill.suggest(_ctx(terms), term="테스트은어", meaning="뜻", aliases="")
    proposal_id = suggestion.data["proposal_id"]

    executor = ToolExecutor(
        resolvers=ResolverRegistry(), tag_provider=InMemoryTagProvider(), tag_definitions={},
        services={"terms": terms},
    )
    spec: ToolSpec = skill.review.__yuni_tool__  # pyright: ignore[reportFunctionMemberAccess]
    ctx = ToolContext(player_id="not-admin", tags=frozenset(), services={"terms": terms}, guild_id="g1")

    outcome = await executor.invoke(spec, ctx, {"proposal_id": proposal_id, "approve": True})

    assert outcome.result is not None and outcome.result.error is not None
    proposal = await terms.get_proposal(proposal_id)
    assert proposal is not None and proposal.status == "suggested"


async def test_review_approve_by_admin(terms: TermsService) -> None:
    skill = _load_skill()
    suggestion = await skill.suggest(_ctx(terms), term="테스트은어", meaning="뜻", aliases="")
    proposal_id = suggestion.data["proposal_id"]

    result = await skill.review(_ctx(terms, player_id="admin1", tags=frozenset({"admin"})), proposal_id=proposal_id, approve=True)

    assert result.error is None
    proposal = await terms.get_proposal(proposal_id)
    assert proposal is not None and proposal.status == "approved"
```

- [ ] **Step 3: 테스트 실행 → 실패 확인**

Run: `uv run pytest skills/chunithm-terms/tests/test_chunithm_terms.py -v`
Expected: FAIL — `skill.py`가 없다.

- [ ] **Step 4: `skills/chunithm-terms/skill.py` 작성**

```python
# skills/chunithm-terms/skill.py
from __future__ import annotations

from core.decorators import tool
from core.tool_types import Display, ToolContext, ToolResult


@tool(slash="용어 뜻")
async def explain(ctx: ToolContext, term: str) -> ToolResult:
    """용어의 뜻, 별칭, 값, 출처를 보여준다."""
    explanation = await ctx.terms.explain(term, ctx)
    if explanation is None:
        return ToolResult(error=f"'{term}'은 모르는 용어다. `/용어 제안`으로 등록을 제안할 수 있다")

    lines = [f"{explanation.canonical} ({explanation.category_label})"]
    if explanation.value is not None:
        lines.append(f"값: {explanation.value:,}")
    if explanation.meaning:
        lines.append(explanation.meaning)
    if explanation.aliases:
        lines.append(f"별칭: {', '.join(explanation.aliases)}")
    lines.append(f"출처: {explanation.source} ({explanation.scope})")

    return ToolResult(
        data={
            "canonical": explanation.canonical, "category": explanation.category_label,
            "value": explanation.value, "scope": explanation.scope,
        },
        display=Display(content="\n".join(lines)),
        display_mode="optional",
    )


@tool(slash="용어 목록")
async def list(ctx: ToolContext, category: str | None = None) -> ToolResult:
    """분류별 용어 목록을 보여준다. 분류를 안 주면 전부 보여준다."""
    try:
        items = await ctx.terms.list_terms(category, ctx)
    except ValueError as exc:
        return ToolResult(error=str(exc))

    lines = [f"{item.canonical} ({item.category_label})" for item in items]
    return ToolResult(
        data={"count": len(items), "terms": [item.canonical for item in items]},
        display=Display(content="\n".join(lines) if lines else "등록된 용어가 없다."),
    )


@tool(slash="용어 제안")
async def suggest(ctx: ToolContext, term: str, meaning: str, aliases: str) -> ToolResult:
    """서버 은어를 제안한다. admin이 승인해야 반영된다."""
    if ctx.guild_id is None:
        return ToolResult(error="서버 안에서만 용어를 제안할 수 있다")
    alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
    proposal_id = await ctx.terms.propose(
        guild_id=ctx.guild_id, term=term, meaning=meaning, aliases=alias_list, suggested_by=ctx.player_id
    )
    return ToolResult(
        data={"proposal_id": proposal_id},
        display=Display(content=f"'{term}' 제안을 등록했다. admin 승인을 기다린다. (제안 id: {proposal_id})"),
        display_mode="optional",
    )


@tool(slash="용어 승인", tags=["admin"], mutates=True, confirm="always")
async def review(ctx: ToolContext, proposal_id: str, approve: bool) -> ToolResult:
    """제안을 승인하거나 거절한다."""
    proposal = await ctx.terms.review(proposal_id, approve=approve, reviewed_by=ctx.player_id)
    if proposal is None:
        return ToolResult(error="그런 제안이 없거나 이미 처리됐다")
    status_text = "승인" if approve else "거절"
    return ToolResult(
        data={"proposal_id": proposal_id, "status": proposal.status},
        display=Display(content=f"'{proposal.term}' 제안을 {status_text}했다."),
        display_mode="optional",
    )
```

(`list`가 파이썬 내장 `list`를 가리는 것은 설계서 자체가 도구 이름을 `list`로 지정했기 때문이다 — 이 파일 안에서만 가리고, 다른 파일에는 영향이 없다. 레지스트리는 함수 이름이 아니라 `<스킬>_<함수>`로 접두사를 붙여 도구 이름을 짓는다.)

**참고 — `test_review_requires_admin_tag_via_executor`가 `core.executor.ToolExecutor`를 거쳐 호출하는 이유**: `review`는 `@tool(tags=["admin"])`이므로, admin이 아닌 플레이어의 호출을 막는 것은 `review` 함수 자신이 아니라 도구 실행기(`core/executor.py`)다. `skill.review(...)`를 직접 부르면 태그 검사를 건너뛰어 버리므로, 이 하나의 테스트만 실행기를 통해 호출해 태그 게이트가 실제로 동작하는지 확인한다. `test_review_approve_by_admin`은 "admin이면 승인 로직이 올바른지"만 보는 테스트라 `skill.review(...)`를 직접 호출해도 된다.

- [ ] **Step 5: 테스트 재실행 → 통과 확인**

Run: `uv run pytest skills/chunithm-terms/tests/test_chunithm_terms.py -v`
Expected: PASS.

- [ ] **Step 6: 전체 테스트 + 린트 + 타입 검사**

Run: `uv run pytest -q && uv run ruff check . && uv run basedpyright`
Expected: 통과.

- [ ] **Step 7: 커밋**

```bash
git add skills/chunithm-terms/
git commit -m "skills(chunithm-terms): 용어 설명, 목록, 서버 은어 제안/승인"
```

---

## 통과 기준

1. `uv sync && uv run pytest && uv run ruff check . && uv run basedpyright`가 전부 통과한다(third_party 제외).
2. 코어/스킬을 추가로 고치지 않고 네 스킬이 레지스트리에 모두 로드된다 — `docker compose up -d --build` 또는 로컬 실행으로 봇을 띄웠을 때 기동 로그에 네 스킬의 로드 실패가 없다.
3. `/계정`, `/기록공개`, `/로그아웃`, `/로그인`, `/곡`, `/상수`, `/별칭 추가`, `/별칭 삭제`, `/보더`, `/남은어택`, `/용어 뜻`, `/용어 목록`, `/용어 제안`, `/용어 승인` 14개 슬래시 명령이 디스코드에 모두 나타난다(동기화 후).
4. 노트 수 3,000개 채보 기준으로 `/보더`가 SSS 14.70어택, SSS+ 5.88어택을 보여준다(셋째 자리에서 버려졌는지 직접 확인).
5. 실제 CHUNITHM-NET 국제판 계정으로 `/로그인` → 성공 메시지 확인 → `/계정`에서 로그인 상태·닉네임 표시 확인 → `/로그아웃` → `/계정`에서 다시 비로그인으로 바뀌는지 확인.

## 디스코드에서 직접 확인할 순서

1. 테스트 서버에서 `/도움말` 대신(아직 없음) 디스코드 클라이언트의 슬래시 명령 목록에 14개 명령이 보이는지 눈으로 확인한다.
2. `/계정` — 아직 로그인 전이면 "로그인: 안 되어있음"이 보이는지 확인한다.
3. `/로그인` — 입력창이 뜨는지, clal 값을 넣고 제출하면 **채널에 아무 메시지도 남지 않고** 본인에게만 결과가 보이는지 확인한다(실제 CHUNITHM-NET 국제판 계정의 clal 값 필요 — 브라우저 로그인 후 개발자 도구 쿠키에서 복사).
4. 로그인 성공 후 `/계정`을 다시 실행해 닉네임이 뜨는지, "마지막 동기화: 아직 동기화한 적 없음"이 뜨는지 확인한다(7단계 전까지는 동기화 기능이 없으므로 이게 맞다).
5. `/기록공개 public:true` → `/계정`에서 "기록 공개: 공개"로 바뀌는지 확인한다. 다시 `false`로 되돌린다.
6. `/곡 song:크로노` 같은 익숙한 곡 이름으로 검색해 정보가 나오는지 확인한다(국제판 수록곡 기준). 국제판에 없는 곡(예: 일본판 전용곡)으로 검색하면 "찾지 못함"이 뜨는지도 확인한다.
7. `/상수 min_const:12 max_const:13 difficulty:마스` 같은 범위로 검색해 목록이 상수순으로 나오는지 확인한다.
8. `/별칭 추가 song:<곡> alias:테스트별칭` → `/곡 song:테스트별칭`으로 같은 곡이 나오는지 확인한다. 다른 곡에 같은 별칭을 또 추가하면 거절되는지 확인한다. `/별칭 삭제 alias:테스트별칭`으로 지운다.
9. `/보더 chart:<곡> 마스`를 랭크 없이 호출해 4개 랭크가 모두 나오는지, `rank:SSS+`를 주면 하나만 나오는지 확인한다.
10. `/남은어택`은 로그인 + 동기화된 기록이 있어야 의미가 있다 — 7단계(`chunithm-sync`) 전까지는 "그 채보를 아직 플레이한 기록이 없다" 오류만 확인할 수 있다(정상이다, 기록이 아직 DB에 없다).
11. `/용어 뜻 term:트플플`, `/용어 뜻 term:오버파워`로 전역 값 용어와 이번에 임시로 넣은 개념 용어가 둘 다 설명되는지 확인한다.
12. `/용어 제안 term:테스트 meaning:테스트뜻 aliases:테스트별칭`으로 제안을 만들고, admin 태그가 없는 계정으로 `/용어 승인`을 시도하면 거절되는지, admin 태그가 있는 계정으로 승인하면 `/용어 뜻 term:테스트`가 "이 서버"로 설명되는지 확인한다.
13. 마지막으로 `/로그아웃` → `/계정`에서 다시 "로그인: 안 되어있음"으로 바뀌는지 확인한다.
