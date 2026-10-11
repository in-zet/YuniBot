# 4단계: 츄니즘 서비스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 3단계 공통 서비스 위에 츄니즘 전용 서비스 네 개(`chunithm_net`, `songdb`, `records`, `terms`)를 올리고, 2단계부터 자리만 있던 표준 인자 타입 여섯 개(`SongQuery`, `ChartRef`, `RankRef`, `DifficultyRef`, `LampRef`, `ScoreRef`)의 해석기를 실제로 등록한다. 이 단계가 끝나면 저장해 둔 CHUNITHM-NET 페이지 3장(`player_data.html`, `rating_detail_best.html`, `rating_detail_recent.html`)으로 계산한 전체 레이팅과 베스트 30·신곡 20 평균이 그 저장본의 표시값과 맞고, 여섯 표준 타입이 별칭·후보 여럿·찾지 못함·국제판 미수록을 설계서대로 처리한다. 스킬(`@tool`)은 이 단계에서 만들지 않는다 — 5단계가 이 서비스들을 `@tool`로 꽂는다.

**Architecture:** 서비스 네 개는 모두 `services/chunithm/`에 새 패키지로 둔다. `songdb`와 `terms`는 둘 다 시작할 때 파일(seed JSON, `terms.yaml`)을 한 번 메모리에 읽어 두는 순수 조회 서비스고, `songdb`만 추가로 `http` 서비스를 통해 Chunimaru songdb를 봇 기동 시 한 번 덧씌운다(주기적 갱신은 이후 단계의 `@schedule` 몫 — 서비스 자신은 스케줄을 갖지 않는다). `records`는 `aiosqlite.Connection`을 받는 상태 있는 서비스로, 3단계 서비스들과 같은 패턴(생성자가 커넥션을 받고 각 메서드가 직접 SQL을 쓰는 클래스)을 따른다. `chunithm_net`은 플레이어별로 `third_party/chuni_penguin`의 `ChunithmNetAdapter`를 만들어 쓰고, 복호화는 `secrets` 서비스를 거친 뒤 그 자리에서만 평문을 쥔다. 네 서비스 모두 `core/bot.py`의 `build_client()`에서 조립해 `ToolExecutor`의 `services` 딕셔너리와 `ResolverRegistry`에 등록한다(3단계의 `PlayerRef`→`players`, `ArcadeRef`→`arcades` 패턴과 동일).

계산이 핵심인 세 가지(채보 레이팅, 허용 어택, 세션 날짜 배정)는 `services/chunithm/records_calc.py`에 상태 없는 순수 함수로 몰아 TDD로 먼저 만든다. 레이팅 자체의 숫자는 재발명하지 않고 vendored `chuni_penguin.calculation.rating.calculate_rating`을 그대로 감싼다 — 975,000점 미만 구간(900k/800k/500k 보간)이 이미 거기 있고, 500,000점 미만만 0이다(사용자 확인: vendored 실제 공식 사용). 이 함수는 둘째 자리에서 버리는데, 저장본으로 검산해 보니 그게 맞는 정밀도다(아래 Task 2 설명과 `docs/design/skills/chunithm-report.md` 참고 — 넷째 자리까지 들고 있다가 마지막에만 버리면 실제 표시값과 어긋난다).

**실제 사이트와 다른 점 하나를 이번 단계에서 고친다.** vendored `parse_music_for_rating`(베스트30/신곡20 파서)이 찾는 `<input name="idx">` 숨은 필드가 지금 국제판 `ratingDetailBest`/`ratingDetailRecent` 페이지에는 없다(오늘 받은 실제 저장본으로 확인: `grep -c 'name="idx"'`가 두 페이지 모두 0). 이대로면 `get_best30()`/`get_new20()`을 부르는 순간 `AttributeError`로 죽는다. `third_party/`는 못 고치므로 `third_party/patches/0003-...patch`를 추가해 `sync_upstream.py`가 재동기화 때 자동 적용하게 한다. `song.id`는 우리 쪽에서 안 쓰므로(제목+난이도로만 매칭) idx가 없으면 0으로 두는 최소 패치다.

**Tech Stack:** Python 3.14, aiosqlite, msgspec, pyyaml(이미 의존성에 있음), `chuni_penguin.calculation.rating`/`chuni_penguin.types`(vendored), httpx(Chunimaru 호출은 기존 `http` 서비스를 통해), pytest, pytest-asyncio, ruff, basedpyright.

**Spec:** `docs/design/00-core.md`("조사로 확인한 전제"의 Chunimaru·chuni-penguin 절, "스킬 모듈 표준"의 표준 인자 타입 표, "이벤트, 예약 작업, 서비스"의 서비스 표, "작업 순서" 4단계), `docs/design/skills/chunithm-terms.md`, `chunithm-song.md`, `chunithm-border.md`, `chunithm-report.md`("전체 레이팅은 베스트30+신곡20의 곡 레이팅 합을 50으로 나눈 값", "곡 레이팅은 소수 둘째 자리에서 버린다" — 2026-10-11에 이 계획과 함께 고친 버전), `chunithm-sync.md`, `chunithm-session.md`("세션 날짜 배정: 새벽 4시 경계, 이력에 없는 갱신은 그 동기화의 마지막 날짜+추정 표시"), `chunithm-account.md`, `CLAUDE.md`. 3단계 결과물(`services/common/*.py`, `core/bot.py`, `core/permissions.py`의 `STATUS_ONLY_TAGS`)이 이번 단계가 올라서는 기반이다.

## Global Constraints

- 스킬(`@tool`, `SKILL.md`, `skills/chunithm-*`)은 이번 단계에 만들지 않는다. 서비스와 해석기만 만든다.
- `third_party/`는 직접 고치지 않는다. 실제 사이트와 다르게 동작해야 하는 부분은 `third_party/patches/000N-*.patch`로 남기고 `scripts/sync_upstream.py`가 재동기화 때 자동 적용한다.
- 레이팅·허용 어택·세션 날짜 배정은 순수 함수로 두고 테스트를 먼저 쓴다(CLAUDE.md "테스트" 절).
- 숫자(상수, 점수, 레이팅, 허용 어택)는 전부 코드가 계산한다. 975,000점 미만 구간은 vendored `chuni_penguin.calculation.rating.calculate_rating`을 그대로 쓴다(재구현하지 않는다). 그 함수가 이미 둘째 자리에서 버리고, 저장본 검산으로 그 정밀도가 맞다는 것까지 확인했다(넷째 자리까지 들고 있다가 마지막에만 버리면 어긋난다).
- 보더는 저스티스 0, 미스 0 기준의 허용 어택 개수 하나만 쓴다. 소수 둘째 자리까지 쓰고 셋째 자리에서 버린다(반올림 금지, `Decimal` + `ROUND_DOWN`으로 부동소수점 오차를 피한다).
- 노트 수가 없는 채보는 "계산 불가"로 표시한다(허용 어택 계산 함수가 `None`을 돌려주는 것으로 표현하고, 호출하는 쪽이 문구로 바꾼다 — 이번 단계는 호출하는 쪽이 없으므로 `None` 반환까지가 테스트 범위다).
- 랭크·난이도·램프·점수 표현은 모델이 해석하지 않는다. 별칭은 `services/chunithm/data/terms.yaml`에만 있고, 값(기준 점수 등)은 YAML에 손으로 적지 않고 vendored `chuni_penguin.types`(`Rank`, `Difficulty`, `ComboLamp`)에서 가져온다 — YAML은 별칭 쪽 매핑만 갖는다.
- clal 쿠키의 복호화는 `secrets`와 `chunithm_net` 서비스 안에서만 한다. 복호화된 평문은 그 함수 스코프를 벗어나 반환되거나 로그에 남지 않는다.
- CHUNITHM-NET 파서 테스트는 `docs/reference/chunithm-net/`의 저장본 3개(`player_data.html`, `rating_detail_best.html`, `rating_detail_recent.html`)로 한다. 실제 사이트에 접속하는 테스트는 만들지 않는다. `chunithm_net` 서비스 자체의 로그인/태그 관리 로직은 가짜 어댑터(fake factory)로 테스트한다 — 실제 어댑터는 Task 1의 패치 검증과 Task 9의 통합 테스트에서만 저장본으로 직접 부른다.
- 스킬 테이블 접두사 규칙은 서비스에는 적용되지 않는다(그 규칙은 "스킬 테이블"에만 적용 — 00-core.md "데이터와 설정" 절). 서비스 테이블은 3단계 전례(`players`, `arcades`, `arcade_aliases`)처럼 평범한 이름을 쓰되, "게임에 딸린 테이블에는 `game` 열을 둔다"(00-core.md "다른 게임으로의 확장 여지")는 그대로 지킨다.
- 서비스 마이그레이션은 `migrations/core/`에 다음 번호(007부터)로 추가한다. 이미 적용한 파일(001~006)은 고치지 않는다.
- `ruff`/`basedpyright`는 `third_party/`를 보지 않는다(이미 설정됨). 새 코드는 `services/chunithm/`, `core/bot.py`, `services/common/players.py`, `third_party/patches/`, `tests/`에만 추가한다.

## Review Focus

- **상수를 못 찾는 채보가 조용히 틀린 값으로 계산되지 않는가**: `songdb`가 로컬 시드와 Chunimaru 양쪽 어디에도 상수가 없는 채보를 만나면(가능성은 낮지만), 레이팅 계산이 `0`이나 임의값으로 새지 않고 `None`/에러로 드러나야 한다(Task 6, Task 9).
- **국제판에 없는 곡이 SongQuery/ChartRef로 절대 안 잡히는가**: 로컬 시드에는 있지만(JP 전용, `available: false`) Chunimaru 목록에는 없는 곡(예: "天体観測")이 "찾지 못함"으로 처리되는지, 어쩌다 세션 간 캐시가 꼬여도 available 쪽이 이기지 않는지(Task 6).
- **Chunimaru 덧씌우기가 실패해도 기동이 안 죽는가**: `refresh_from_chunimaru`가 네트워크 오류로 실패해도 `build_client()` 전체가 죽지 않고 로컬 시드 기준 `available` 플래그로 degrade하는지(Task 6, Task 10).
- **허용 어택 버림이 반올림으로 새지 않는가**: `float` 연산이 아니라 `Decimal` 경로를 타는지, `x.xx9` 꼴의 경계값에서 올라가지 않는지(Task 2).
- **같은 동기화 안에서 이력에 없는 갱신(추정 세션)과 자정 넘긴 플레이가 동시에 있을 때 날짜 배정이 섞이지 않는가**: `assign_session_date`(새벽 4시 경계)와 "이력에 없으면 마지막 날짜+추정"이 서로 다른 함수/경로라 한 동기화에 둘 다 있어도 각자 맞게 배정되는지(Task 2, Task 9).

---

## Task 1: vendored 파서 idx 패치 + 재동기화 + 리그레션 테스트

**Files:**
- Create: `third_party/patches/0003-chunithm-net-rating-detail-missing-idx.patch`
- Modify: `third_party/patches/README.md`
- Modify: `tests/third_party/test_chunithm_net_vendoring.py`
- Modify (재생성): `third_party/chuni_penguin/**`(`scripts/sync_upstream.py` 실행 결과)

**Interfaces:**
- Consumes: 없음(저장본 `docs/reference/chunithm-net/rating_detail_best.html`, `rating_detail_recent.html`은 이미 저장돼 있다)
- Produces: 패치된 `chuni_penguin.adapters.chunithm_net.parser.parse_music_for_rating`이 `idx` 필드 없이도 죽지 않고 `Song(id=0, ...)`을 돌려준다. Task 9의 통합 테스트가 이 동작에 의존한다.

- [ ] **Step 1: 문제를 재현하는 실패 테스트 작성**

```python
# tests/third_party/test_chunithm_net_vendoring.py 에 추가
from chuni_penguin.adapters.chunithm_net.parser import parse_music_for_rating

BEST_FIXTURE = Path("docs/reference/chunithm-net/rating_detail_best.html")
RECENT_FIXTURE = Path("docs/reference/chunithm-net/rating_detail_recent.html")


def test_parses_best30_fixture_without_idx_field():
    """국제판 ratingDetailBest 페이지에는 <input name="idx">가 없다(사이트가 바뀜).
    패치 0003 전에는 AttributeError로 죽는다."""
    html = BEST_FIXTURE.read_text(encoding="utf-8")
    records = parse_music_for_rating(LexborHTMLParser(html))
    assert len(records) == 30
    assert all(r.song.id == 0 for r in records)
    assert all(r.song.title for r in records)
    assert all(r.score > 0 for r in records)


def test_parses_new20_fixture_without_idx_field():
    html = RECENT_FIXTURE.read_text(encoding="utf-8")
    records = parse_music_for_rating(LexborHTMLParser(html))
    assert len(records) == 20
    assert all(r.song.id == 0 for r in records)
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/third_party/test_chunithm_net_vendoring.py -v
```

Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'attrs'` (현재 vendored 코드가 `x.css_first("form input[name=idx]").attrs["value"]`에서 `None`을 역참조)

- [ ] **Step 3: 패치 파일 작성**

현재 `third_party/chuni_penguin/adapters/chunithm_net/parser.py`의 `parse_music_for_rating`은 0001/0002 패치가 손대지 않은 영역이라 업스트림 원본과 바이트가 같다. 그 사실을 이용해 지금 vendored 파일을 "패치 전" 기준으로 diff를 뜬다.

```bash
cd /home/dn/projects/YuniBot
cp third_party/chuni_penguin/adapters/chunithm_net/parser.py /tmp/parser_before.py
```

`third_party/chuni_penguin/adapters/chunithm_net/parser.py`에서 아래 한 줄을:

```python
        song = Song(
            id=int(str(x.css_first("form input[name=idx]").attrs["value"])),
            title=x.css_first(".music_title, .musiclist_worldsend_title").text(),
        )
```

아래로 바꾼다:

```python
        idx_elem = x.css_first("form input[name=idx]")
        song = Song(
            # YUNIBOT PATCH: 국제판 ratingDetailBest/ratingDetailRecent 페이지에는
            # idx 숨은 필드가 없다(사이트가 바뀜). 우리는 song.id를 안 쓰고 제목+
            # 난이도로만 매칭하므로 0으로 둬도 안전하다.
            id=int(str(idx_elem.attrs["value"])) if idx_elem is not None else 0,
            title=x.css_first(".music_title, .musiclist_worldsend_title").text(),
        )
```

patch 생성:

```bash
diff -u /tmp/parser_before.py third_party/chuni_penguin/adapters/chunithm_net/parser.py \
  | sed 's|/tmp/parser_before.py|a/chuni_penguin/adapters/chunithm_net/parser.py|; s|third_party/chuni_penguin/adapters/chunithm_net/parser.py|b/chuni_penguin/adapters/chunithm_net/parser.py|' \
  > third_party/patches/0003-chunithm-net-rating-detail-missing-idx.patch
git checkout third_party/chuni_penguin/adapters/chunithm_net/parser.py
```

(마지막 줄로 손으로 고친 vendored 파일을 되돌린다 — vendored 디렉터리는 `sync_upstream.py`가 패치를 적용해 다시 만들어야 "손으로 고치지 않는다" 규칙을 지킨다.)

- [ ] **Step 4: `third_party/patches/README.md`에 패치 설명 추가**

```markdown
## 0003-chunithm-net-rating-detail-missing-idx.patch

`parse_music_for_rating`(`get_best30()`/`get_new20()`이 쓰는 파서)은 각 곡 폼 안의
`<input name="idx">` 숨은 필드에서 `song.id`를 읽는다. 그런데 국제판
`ratingDetailBest`/`ratingDetailRecent` 페이지의 실제 마크업에는(2026-10-11 저장본
기준) 이 필드가 없다 — 패치 전에는 `AttributeError`로 죽는다. 이 패치는 필드가
없으면 `song.id = 0`으로 둔다. 우리 쪽 `songdb`/`records` 서비스는 `song.id`를
전혀 안 쓰고 제목+난이도로만 매칭하므로 안전하다(단, vendored
`get_rating_breakdown`처럼 `song.id`로 중복을 거르는 메서드는 이 두 페이지를 통해
호출하면 여러 곡이 `id=0`으로 뭉쳐 보일 수 있다 — 우리는 `get_best30()`/
`get_new20()`만 직접 쓰고 `get_rating_breakdown()`은 쓰지 않는다).
```

- [ ] **Step 5: 재동기화로 패치 적용 확인**

```bash
uv run python3 scripts/sync_upstream.py --ref 6c926755f296476850adbb59a98651156ba7c7f7
```

Expected: 종료 코드 0. `git diff --stat third_party/chuni_penguin/UPSTREAM`에 패치 3개가 적용됐다고 기록돼야 한다.

- [ ] **Step 6: 테스트 통과 확인**

```bash
uv run pytest tests/third_party/test_chunithm_net_vendoring.py -v
```

Expected: PASS (기존 2개 + 새 2개, 총 4개 이상)

- [ ] **Step 7: 커밋**

```bash
git add third_party/patches/0003-chunithm-net-rating-detail-missing-idx.patch \
  third_party/patches/README.md third_party/chuni_penguin tests/third_party/test_chunithm_net_vendoring.py
git commit -m "third_party: patch parse_music_for_rating for missing idx field on live site"
```

---

## Task 2: records_calc — 순수 계산 함수 (레이팅, 허용 어택, 세션 날짜)

**Files:**
- Create: `services/chunithm/__init__.py`
- Create: `services/chunithm/records_calc.py`
- Create: `tests/services/chunithm/__init__.py`
- Create: `tests/services/chunithm/test_records_calc.py`

**Interfaces:**
- Consumes: `chuni_penguin.calculation.rating.calculate_rating`
- Produces: `chart_rating(score: int, const: float | None) -> Decimal`(둘째 자리에서 버린 값), `average_rating(ratings: Sequence[Decimal]) -> Decimal`(합산 후 둘째 자리에서 버림), `allowed_attacks(note_count: int | None, target_score: int) -> Decimal | None`, `remaining_to_target(note_count: int | None, current_score: int, target_score: int) -> tuple[int, Decimal] | None`, `assign_session_date(played_at: datetime, *, boundary_hour: int = 4) -> date`. Task 9(`records` 서비스)가 전부 쓴다.

**레이팅 정밀도(저장본으로 검산해 확정)**: 곡 레이팅은 둘째 자리에서 버린다, 넷째 자리까지 들고 있다가 마지막에만 버리지 않는다. 계획을 쓰는 중 실제 저장본 세 장(같은 시점에 받은 프로필+베스트30+신곡20)으로 두 방식을 직접 계산해 봤다: 곡마다 둘째 자리 버림 후 합산하면 표시 레이팅(16.94)과 정확히 맞고, 넷째 자리를 유지하다 마지막에만 버리면 16.95로 어긋난다. `docs/design/skills/chunithm-report.md`도 이미 이 정밀도로 고쳤다(2026-10-11). 그래서 `chart_rating`은 새로 계산하지 않고 vendored `calculate_rating`(이미 둘째 자리 버림)을 그대로 감싼다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/chunithm/test_records_calc.py
from datetime import date, datetime
from decimal import Decimal

from services.chunithm.records_calc import (
    allowed_attacks,
    assign_session_date,
    average_rating,
    chart_rating,
    remaining_to_target,
)


def test_chart_rating_matches_sssp_example():
    # SSS+(1,009,000)에서 상수 12.5짜리 채보: whole = il10000 + 21500
    # il10000 = 125000 -> (125000+21500)//100/100 = 14.65 (vendored calculate_rating)
    assert chart_rating(1_009_000, 12.5) == Decimal("14.65")


def test_chart_rating_below_500k_is_zero():
    assert chart_rating(100_000, 13.0) == Decimal("0")


def test_chart_rating_none_const_treated_as_zero_internal_level():
    # internal_level=None은 vendored 쪽에서 0으로 취급한다(il10000=0).
    result = chart_rating(1_009_000, None)
    assert result == Decimal("2.15")


def test_average_rating_divides_by_full_length_and_truncates_to_2dp():
    ratings = [Decimal("14.65")] * 30 + [Decimal("14.00")] * 20
    # (14.65*30 + 14.00*20) / 50 = (439.5 + 280) / 50 = 719.5/50 = 14.39
    assert average_rating(ratings) == Decimal("14.39")


def test_average_rating_truncates_not_rounds():
    # 평균이 16.948로 나오는 경우, 버림이면 16.94, 반올림이면 16.95다. 반올림 쪽은
    # 실제 CHUNITHM-NET 표시값과 저장본 검산에서 어긋났던 사례라 버림을 명시적으로 고정한다.
    ratings = [Decimal("16.948")] * 50  # 합=847.40, 평균=16.948(이미 2dp보다 세밀한 입력)
    assert average_rating(ratings) == Decimal("16.94")


def test_allowed_attacks_matches_border_doc_examples():
    # 노트 3,000개: SSS(1,007,500) -> 14.70, SSS+(1,009,000) -> 5.88
    assert allowed_attacks(3000, 1_007_500) == Decimal("14.70")
    assert allowed_attacks(3000, 1_009_000) == Decimal("5.88")


def test_allowed_attacks_truncates_down_not_round():
    # (1,010,000-1,007,500)*3050/510_000 = 2500*3050/510000 = 14.95098...
    assert allowed_attacks(3050, 1_007_500) == Decimal("14.95")


def test_allowed_attacks_none_when_note_count_missing():
    assert allowed_attacks(None, 1_007_500) is None
    assert allowed_attacks(0, 1_007_500) is None


def test_remaining_to_target_already_met_returns_non_positive_and_zero_attacks():
    remaining_score, attacks = remaining_to_target(3000, current_score=1_008_000, target_score=1_007_500)
    assert remaining_score == -500
    assert attacks == Decimal("0.00")


def test_remaining_to_target_computes_score_and_attacks():
    remaining_score, attacks = remaining_to_target(3000, current_score=1_000_000, target_score=1_007_500)
    assert remaining_score == 7_500
    # 7500*3000/510000 = 44.117647... -> 44.11
    assert attacks == Decimal("44.11")


def test_remaining_to_target_none_when_note_count_missing():
    assert remaining_to_target(None, current_score=1_000_000, target_score=1_007_500) is None


def test_assign_session_date_before_boundary_belongs_to_previous_day():
    played_at = datetime(2026, 10, 5, 2, 30)  # 새벽 2시 30분
    assert assign_session_date(played_at) == date(2026, 10, 4)


def test_assign_session_date_at_boundary_belongs_to_same_day():
    played_at = datetime(2026, 10, 5, 4, 0)
    assert assign_session_date(played_at) == date(2026, 10, 5)


def test_assign_session_date_after_boundary_belongs_to_same_day():
    played_at = datetime(2026, 10, 5, 23, 59)
    assert assign_session_date(played_at) == date(2026, 10, 5)


def test_assign_session_date_respects_custom_boundary():
    played_at = datetime(2026, 10, 5, 3, 0)
    assert assign_session_date(played_at, boundary_hour=2) == date(2026, 10, 5)
    assert assign_session_date(played_at, boundary_hour=4) == date(2026, 10, 4)
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/services/chunithm/test_records_calc.py -v
```

Expected: FAIL (`services.chunithm.records_calc` 모듈 없음)

- [ ] **Step 3: 구현**

```python
# services/chunithm/__init__.py
```

```python
# services/chunithm/records_calc.py
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

from chuni_penguin.calculation.rating import calculate_rating

_MAX_SCORE = 1_010_000
_ATTACK_DEDUCTION_DENOMINATOR = 510_000


def chart_rating(score: int, const: float | None) -> Decimal:
    """채보 하나의 레이팅. vendored calculate_rating을 그대로 감싼다 — 그 함수가
    이미 둘째 자리에서 버린다(1/10000 단위 정수를 //100으로 두 자리 버린 뒤 /100).
    975,000점 미만 구간(900k/800k/500k 보간, 500k 미만은 0)도 같은 함수 안에 있다
    (사용자 확인: vendored 실제 공식 사용, 재구현하지 않는다).

    둘째 자리에서 버리는 것이 맞다 — 넷째 자리까지 들고 있다가 합산 뒤에만 버리면
    안 된다. 실제 저장본(베스트30+신곡20+프로필, 같은 로그인 시점)으로 검산했을 때
    곡마다 둘째 자리 버림 후 합산해야 표시 레이팅과 맞았고, 넷째 자리를 유지하면
    어긋났다. docs/design/skills/chunithm-report.md도 이 정밀도로 고쳤다."""
    return calculate_rating(score, const)


def average_rating(ratings: Sequence[Decimal]) -> Decimal:
    """베스트30 평균, 신곡20 평균, 전체 레이팅(50으로 나눔)에 공통으로 쓴다. 입력은
    이미 chart_rating()으로 둘째 자리까지 버려진 값들이고, 합산한 평균도 둘째
    자리에서 버린다(위 chart_rating 설명 참고 — 더 가는 단위를 유지할 근거가 없다)."""
    if not ratings:
        return Decimal("0.00")
    total = sum(ratings, Decimal(0))
    return (total / len(ratings)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def allowed_attacks(note_count: int | None, target_score: int) -> Decimal | None:
    """저스티스 0, 미스 0 기준의 허용 어택 개수. 노트 수가 없으면(계산 불가) None."""
    if not note_count:
        return None
    deduction = Decimal(_MAX_SCORE - target_score) * note_count
    return (deduction / Decimal(_ATTACK_DEDUCTION_DENOMINATOR)).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )


def remaining_to_target(
    note_count: int | None, *, current_score: int, target_score: int
) -> tuple[int, Decimal] | None:
    """목표까지 남은 점수와 어택. 노트 수가 없으면 None. 이미 목표를 넘었으면
    (음수 또는 0 남은 점수, 0.00어택)을 돌려준다 — 호출하는 쪽이 "이미 달성"으로
    말로 바꾼다."""
    if not note_count:
        return None
    remaining_score = target_score - current_score
    if remaining_score <= 0:
        return remaining_score, Decimal("0.00")
    attacks = (Decimal(remaining_score) * note_count / Decimal(_ATTACK_DEDUCTION_DENOMINATOR)).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    return remaining_score, attacks


def assign_session_date(played_at: datetime, *, boundary_hour: int = 4) -> date:
    """자정을 넘긴 플레이는 날짜 경계(기본 새벽 4시) 전까지 전날 세션에 붙는다
    (chunithm-session.md). 경계 시각 자체는 "그날"에 속한다(>=)."""
    if played_at.hour < boundary_hour:
        return (played_at - _ONE_DAY).date()
    return played_at.date()


from datetime import timedelta as _timedelta  # noqa: E402 - 위 함수가 먼저 읽히게 아래에 둔다

_ONE_DAY = _timedelta(days=1)
```

(마지막 두 줄처럼 함수 뒤에 import를 두는 것은 어색하니, 실제로 작성할 때는 `_ONE_DAY = timedelta(days=1)`을 파일 상단 import 바로 아래(`_MAX_SCORE` 옆)에 두고 `from datetime import date, datetime, timedelta`로 한 번에 import한다 — 위 코드 블록은 설명 편의상 나눠 적었을 뿐이다.)

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/chunithm/test_records_calc.py -v
```

Expected: PASS (14개)

- [ ] **Step 5: 커밋**

```bash
git add services/chunithm/__init__.py services/chunithm/records_calc.py \
  tests/services/chunithm/__init__.py tests/services/chunithm/test_records_calc.py
git commit -m "services: add pure rating/border/session-date calculation functions"
```

---

## Task 3: players 서비스 — 상태 태그 전용 메서드

**Files:**
- Modify: `services/common/players.py`
- Modify: `tests/services/test_players.py`

**Interfaces:**
- Consumes: `core.permissions.STATUS_ONLY_TAGS`
- Produces: `PlayersService.set_status_tag(player_id, tag, *, source) -> None`(STATUS_ONLY_TAGS에 **없는** 태그를 주면 거부), `.revoke_status_tag(player_id, tag) -> None`(같음). Task 8(`chunithm_net`)이 `chunithm-linked`를, Task 9(`records`)가 `chunithm-synced`를 이걸로 붙이고 뗀다.

기존 `grant_tag`/`revoke_tag`는 "상태 태그는 손으로 못 바꾼다"를 지키려고 `STATUS_ONLY_TAGS`를 **거부**한다(admin 스킬의 수동 부여용). 서비스가 자동으로 붙이는 경로는 반대로 **그 태그만** 허용하는 새 메서드가 필요하다 — 실수로 `grant_tag("admin", ...)`처럼 일반 태그를 이 경로로 붙이면 "상태 태그 보호"가 의미 없어지기 때문이다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/test_players.py 에 추가
async def test_set_status_tag_allows_only_status_tags(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()

    await service.set_status_tag(player.id, "chunithm-linked", source="chunithm_net:login")
    assert await service.tags_for(player.id) == {"chunithm-linked"}
    assert await service.tag_source(player.id, "chunithm-linked") == "chunithm_net:login"

    with pytest.raises(ValueError, match="상태 태그가 아니다"):
        await service.set_status_tag(player.id, "admin", source="x")


async def test_revoke_status_tag_allows_only_status_tags(conn: aiosqlite.Connection) -> None:
    service = PlayersService(conn)
    player = await service.create_player()
    await service.set_status_tag(player.id, "chunithm-synced", source="records:first_sync")

    await service.revoke_status_tag(player.id, "chunithm-synced")
    assert await service.tags_for(player.id) == set()

    with pytest.raises(ValueError, match="상태 태그가 아니다"):
        await service.revoke_status_tag(player.id, "tester")
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/services/test_players.py -v -k status_tag
```

Expected: FAIL (`set_status_tag`/`revoke_status_tag` 없음)

- [ ] **Step 3: 구현**

```python
# services/common/players.py — PlayersService 클래스 끝(resolve 메서드 뒤)에 추가
    async def set_status_tag(self, player_id: str, tag: str, *, source: str) -> None:
        """grant_tag와 반대 방향의 가드: STATUS_ONLY_TAGS **밖**의 태그는 거부한다.
        chunithm_net/records 서비스가 로그인·첫 동기화를 감지했을 때만 쓴다."""
        if tag not in STATUS_ONLY_TAGS:
            raise ValueError(f"'{tag}'는 상태 태그가 아니다: {sorted(STATUS_ONLY_TAGS)}")
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_tags (player_id, tag, source, granted_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (player_id, tag) DO UPDATE SET source = excluded.source, "
            "granted_at = excluded.granted_at",
            (player_id, tag, source, now),
        )
        await self._conn.commit()

    async def revoke_status_tag(self, player_id: str, tag: str) -> None:
        if tag not in STATUS_ONLY_TAGS:
            raise ValueError(f"'{tag}'는 상태 태그가 아니다: {sorted(STATUS_ONLY_TAGS)}")
        await self._conn.execute(
            "DELETE FROM player_tags WHERE player_id = ? AND tag = ?", (player_id, tag)
        )
        await self._conn.commit()
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/test_players.py -v
```

Expected: PASS (전체, 기존 14개 + 새 2개)

- [ ] **Step 5: 커밋**

```bash
git add services/common/players.py tests/services/test_players.py
git commit -m "services(players): add status-tag-only grant/revoke for service callers"
```

---

## Task 4: 마이그레이션 — chunithm_net 쿠키, records 테이블

**Files:**
- Create: `migrations/core/007_chunithm_net_cookies.sql`
- Create: `migrations/core/008_records.sql`

**Interfaces:**
- Consumes: 없음
- Produces: `chunithm_net_cookies`, `records_sessions`, `records_pb`, `records_sync_log` 테이블. Task 8이 첫 테이블을, Task 9가 나머지 세 개를 쓴다.

- [ ] **Step 1: `chunithm_net_cookies` 마이그레이션 작성**

```sql
-- migrations/core/007_chunithm_net_cookies.sql
CREATE TABLE chunithm_net_cookies (
    player_id TEXT PRIMARY KEY REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    encrypted_cookie BLOB NOT NULL,
    linked_at TEXT NOT NULL,
    last_checked_at TEXT
);
```

- [ ] **Step 2: `records` 마이그레이션 작성**

```sql
-- migrations/core/008_records.sql
CREATE TABLE records_sessions (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    play_date TEXT NOT NULL,
    estimated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (player_id, game, play_date)
);

CREATE TABLE records_pb (
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    song_title TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    score INTEGER NOT NULL,
    rank TEXT NOT NULL,
    clear_lamp TEXT NOT NULL,
    combo_lamp TEXT NOT NULL,
    chain_lamp TEXT NOT NULL,
    const REAL,
    rating TEXT NOT NULL,
    session_id TEXT REFERENCES records_sessions(id),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, game, song_title, difficulty)
);

CREATE TABLE records_sync_log (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    synced_at TEXT NOT NULL,
    nickname TEXT,
    displayed_rating TEXT,
    computed_rating TEXT NOT NULL,
    rating_mismatch INTEGER NOT NULL DEFAULT 0,
    is_first_sync INTEGER NOT NULL DEFAULT 0
);
```

- [ ] **Step 3: 마이그레이션이 깨끗이 적용되는지 확인**

```python
# 임시 확인(커밋 대상 아님) — 둘 다 단독 executescript로 깨지지 않는지만 본다
uv run python3 -c "
import asyncio, aiosqlite
from pathlib import Path

async def main():
    conn = await aiosqlite.connect(':memory:')
    await conn.executescript(Path('migrations/core/001_players.sql').read_text())
    await conn.executescript(Path('migrations/core/007_chunithm_net_cookies.sql').read_text())
    await conn.executescript(Path('migrations/core/008_records.sql').read_text())
    print('ok')

asyncio.run(main())
"
```

Expected: `ok` 출력, 예외 없음

- [ ] **Step 4: 커밋**

```bash
git add migrations/core/007_chunithm_net_cookies.sql migrations/core/008_records.sql
git commit -m "migrations: add chunithm_net cookie storage and records tables"
```

---

## Task 5: terms 서비스 — terms.yaml + 값 용어 해석기(Rank/Difficulty/Lamp/Score)

**Files:**
- Create: `services/chunithm/data/terms.yaml`
- Create: `services/chunithm/terms.py`
- Create: `migrations/core/009_terms_server.sql`
- Create: `tests/services/chunithm/test_terms.py`

**Interfaces:**
- Consumes: `chuni_penguin.types.Rank`, `Difficulty`, `ComboLamp`, `core.resolvers.ResolutionError`
- Produces: `ResolvedRank(canonical: str, min_score: int)`, `ResolvedDifficulty(canonical: str)`, `ResolvedLamp(canonical: str, min_score: int | None)`, `TermsService`(`resolve_rank`, `resolve_difficulty`, `resolve_lamp`, `resolve_score`, `difficulty_token(text: str) -> str | None`). `resolve_rank`/`resolve_difficulty`/`resolve_lamp`는 각각 `RankRef`/`DifficultyRef`/`LampRef`의 `Resolver` 구현(`resolve(raw, ctx)`로 어댑터만 씌운다)이고, `resolve_score`가 `ScoreRef`의 `Resolver` 구현이다. Task 7(`ChartRef` 해석기)이 `difficulty_token`을 쓴다.

**terms.yaml 별칭은 사용자가 확인한 것만 넣는다**(채팅에서 제안한 후보 목록 — 랭크 6개, 난이도 6개, 램프 3개 — 를 사용자가 승인한 상태로 진행한다. 실행 중 사용자가 다른 목록을 준다면 이 파일 내용만 바꾸면 되고, 코드는 안 바뀐다).

- [ ] **Step 1: `terms.yaml` 작성**

```yaml
# services/chunithm/data/terms.yaml
# 값 용어(랭크·난이도·램프)의 "값"은 여기 안 쓴다 — vendored chuni_penguin.types의
# Rank/Difficulty/ComboLamp가 가진 값을 그대로 쓴다. 여기는 별칭만 전역으로 둔다.
# 서버 은어는 이 파일이 아니라 terms_server 테이블(제안·승인)에 있다.
ranks:
  "SSS+": [트플플]
  "SSS": [트리플, 트플]
  "SS+": [더블플]
  "SS": [더블]
  "S+": [에스플]
  "S": [에스]

difficulties:
  "MASTER": [마스]
  "EXPERT": [엑스퍼, 엑스퍼트]
  "ADVANCED": [어드, 어드밴스]
  "BASIC": [베이직]
  "ULTIMA": [울티마, 얼티마]
  "WORLD'S END": [월드엔드, 월엔]

lamps:
  "AJC": [이론치, 만점]
  "ALL JUSTICE": [올저, 올저스티스]
  "FULL COMBO": [풀콤, 풀콤보]
```

- [ ] **Step 2: `terms_server` 마이그레이션 작성**

```sql
-- migrations/core/009_terms_server.sql
CREATE TABLE terms_server (
    id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    term TEXT NOT NULL,
    meaning TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    suggested_by TEXT NOT NULL REFERENCES players(id),
    status TEXT NOT NULL DEFAULT 'suggested',
    reviewed_by TEXT REFERENCES players(id),
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
```

(이번 단계는 이 테이블을 쓰는 코드를 만들지 않는다 — `chunithm-terms` 스킬이 5단계 이후에 쓸 자리만 미리 만든다. CLAUDE.md "작업 순서": "다음 단계에서 쓸 자리만 비워 둔다".)

- [ ] **Step 3: 실패하는 테스트 작성**

```python
# tests/services/chunithm/test_terms.py
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.terms import TermsService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})


@pytest.fixture
def terms() -> TermsService:
    return TermsService()


async def test_resolve_rank_canonical_name(terms: TermsService) -> None:
    resolved = await terms.resolve_rank("SSS+", _CTX)
    assert resolved.canonical == "SSS+"
    assert resolved.min_score == 1_009_000


async def test_resolve_rank_korean_alias(terms: TermsService) -> None:
    resolved = await terms.resolve_rank("트플플", _CTX)
    assert resolved.canonical == "SSS+"
    assert resolved.min_score == 1_009_000


async def test_resolve_rank_case_insensitive_canonical(terms: TermsService) -> None:
    resolved = await terms.resolve_rank("sss", _CTX)
    assert resolved.canonical == "SSS"
    assert resolved.min_score == 1_007_500


async def test_resolve_rank_unknown_raises_with_no_candidates(terms: TermsService) -> None:
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await terms.resolve_rank("이상한말", _CTX)


async def test_resolve_difficulty_korean_alias(terms: TermsService) -> None:
    resolved = await terms.resolve_difficulty("마스", _CTX)
    assert resolved.canonical == "MASTER"


async def test_resolve_difficulty_short_code(terms: TermsService) -> None:
    resolved = await terms.resolve_difficulty("MAS", _CTX)
    assert resolved.canonical == "MASTER"


async def test_resolve_difficulty_unknown_raises(terms: TermsService) -> None:
    with pytest.raises(ResolutionError):
        await terms.resolve_difficulty("모르는난이도", _CTX)


async def test_resolve_lamp_ajc_has_min_score(terms: TermsService) -> None:
    resolved = await terms.resolve_lamp("이론치", _CTX)
    assert resolved.canonical == "AJC"
    assert resolved.min_score == 1_010_000


async def test_resolve_lamp_full_combo_has_no_min_score(terms: TermsService) -> None:
    resolved = await terms.resolve_lamp("풀콤", _CTX)
    assert resolved.canonical == "FULL COMBO"
    assert resolved.min_score is None


async def test_resolve_score_plain_comma_number(terms: TermsService) -> None:
    assert await terms.resolve_score("1,007,500", _CTX) == 1_007_500


async def test_resolve_score_k_shorthand(terms: TermsService) -> None:
    assert await terms.resolve_score("1007.5k", _CTX) == 1_007_500


async def test_resolve_score_korean_number_words(terms: TermsService) -> None:
    assert await terms.resolve_score("100만7천5백", _CTX) == 1_007_500


async def test_resolve_score_korean_number_words_with_spaces(terms: TermsService) -> None:
    assert await terms.resolve_score("100만 7천 5백", _CTX) == 1_007_500


async def test_resolve_score_rank_name(terms: TermsService) -> None:
    assert await terms.resolve_score("트플플", _CTX) == 1_009_000


async def test_resolve_score_ajc_lamp_name(terms: TermsService) -> None:
    assert await terms.resolve_score("이론치", _CTX) == 1_010_000


async def test_resolve_score_unparseable_raises(terms: TermsService) -> None:
    with pytest.raises(ResolutionError, match="읽을 수 없는 점수"):
        await terms.resolve_score("풀콤", _CTX)  # 점수가 안 고정된 램프는 ScoreRef로 못 씀


async def test_difficulty_token_recognizes_alias_and_short_code(terms: TermsService) -> None:
    assert terms.difficulty_token("마스") == "MASTER"
    assert terms.difficulty_token("MAS") == "MASTER"
    assert terms.difficulty_token("크로노") is None
```

- [ ] **Step 4: 실패 확인**

```bash
uv run pytest tests/services/chunithm/test_terms.py -v
```

Expected: FAIL (`services.chunithm.terms` 없음)

- [ ] **Step 5: 구현**

```python
# services/chunithm/terms.py
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from chuni_penguin.types import ComboLamp, Difficulty, Rank
from core.resolvers import ResolutionError
from core.tool_types import ToolContext

_DATA_PATH = Path(__file__).parent / "data" / "terms.yaml"

_HANGUL_SCALE: dict[str, int] = {"만": 10_000, "천": 1_000, "백": 100, "십": 10}

# ComboLamp에 "값"(score)이 붙는 건 AJC(만점)뿐이다. FC/AJ는 점수가 고정돼 있지 않다.
_LAMP_MIN_SCORE: dict[str, int | None] = {
    "AJC": 1_010_000,
    "ALL JUSTICE": None,
    "FULL COMBO": None,
}

# ComboLamp.__str__()의 "AJC"/"ALL JUSTICE"/"FULL COMBO" 표기와 terms.yaml의 canonical
# 키를 맞춰 준다(ComboLamp.none은 값 용어가 아니라서 뺀다).
_LAMP_CANONICALS: dict[str, ComboLamp] = {
    str(lamp): lamp
    for lamp in ComboLamp
    if lamp is not ComboLamp.none
}


@dataclass(slots=True, kw_only=True)
class ResolvedRank:
    canonical: str
    min_score: int


@dataclass(slots=True, kw_only=True)
class ResolvedDifficulty:
    canonical: str


@dataclass(slots=True, kw_only=True)
class ResolvedLamp:
    canonical: str
    min_score: int | None


def _load_alias_map(data: dict[str, Any], key: str) -> dict[str, str]:
    """{"SSS+": ["트플플"]} 형태를 {"트플플": "SSS+", "sss+": "SSS+"}로 뒤집는다
    (별칭과 canonical 자기 자신 둘 다 소문자 키로 넣어 대소문자 무시 조회를 만든다)."""
    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in data.get(key, {}).items():
        alias_to_canonical[canonical.lower()] = canonical
        for alias in aliases:
            alias_to_canonical[alias.lower()] = canonical
    return alias_to_canonical


class TermsService:
    """값 용어(랭크·난이도·램프·점수)의 별칭을 해석한다. 값 자체(기준 점수 등)는
    vendored chuni_penguin.types에서 가져오고 여기서 다시 적지 않는다."""

    def __init__(self, data_path: Path = _DATA_PATH) -> None:
        raw = yaml.safe_load(data_path.read_text(encoding="utf-8")) or {}
        self._rank_aliases = _load_alias_map(raw, "ranks")
        self._difficulty_aliases = _load_alias_map(raw, "difficulties")
        self._lamp_aliases = _load_alias_map(raw, "lamps")

    async def resolve_rank(self, raw: str, ctx: ToolContext) -> ResolvedRank:
        canonical = self._rank_aliases.get(raw.strip().lower())
        if canonical is None:
            raise ResolutionError(message="찾지 못함")
        for rank in Rank:
            if str(rank) == canonical:
                return ResolvedRank(canonical=canonical, min_score=rank.min_score)
        raise ResolutionError(message="찾지 못함")

    async def resolve_difficulty(self, raw: str, ctx: ToolContext) -> ResolvedDifficulty:
        token = self.difficulty_token(raw)
        if token is None:
            raise ResolutionError(message="모르는 난이도")
        return ResolvedDifficulty(canonical=token)

    def difficulty_token(self, text: str) -> str | None:
        """Korean 별칭(terms.yaml) 또는 vendored Difficulty가 아는 영문 표기/짧은
        코드("MASTER"/"MAS")를 정식 난이도 문자열로 바꾼다. 모르면 None(ChartRef
        해석기가 "이 토큰은 난이도가 아니라 곡 이름의 일부"로 되돌릴 수 있게)."""
        canonical = self._difficulty_aliases.get(text.strip().lower())
        if canonical is not None:
            return canonical
        try:
            return str(Difficulty(text.strip()))
        except ValueError:
            return None

    async def resolve_lamp(self, raw: str, ctx: ToolContext) -> ResolvedLamp:
        canonical = self._lamp_aliases.get(raw.strip().lower())
        if canonical is None:
            raise ResolutionError(message="모르는 램프")
        return ResolvedLamp(canonical=canonical, min_score=_LAMP_MIN_SCORE[canonical])

    async def resolve_score(self, raw: str, ctx: ToolContext) -> int:
        text = raw.strip()

        numeric = _parse_numeric_score(text)
        if numeric is not None:
            return numeric

        rank_canonical = self._rank_aliases.get(text.lower())
        if rank_canonical is not None:
            resolved = await self.resolve_rank(text, ctx)
            return resolved.min_score

        lamp_canonical = self._lamp_aliases.get(text.lower())
        if lamp_canonical is not None:
            min_score = _LAMP_MIN_SCORE[lamp_canonical]
            if min_score is not None:
                return min_score

        raise ResolutionError(message="읽을 수 없는 점수")


def _parse_numeric_score(text: str) -> int | None:
    stripped = text.replace(",", "")

    if stripped.lower().endswith("k"):
        try:
            return int(Decimal(stripped[:-1]) * 1000)
        except Exception:
            return None

    if stripped.isdigit():
        return int(stripped)

    hangul = _parse_hangul_number(text.replace(" ", "").replace(",", ""))
    if hangul is not None:
        return hangul

    return None


def _parse_hangul_number(text: str) -> int | None:
    if not text or not any(ch in _HANGUL_SCALE for ch in text):
        return None

    total = 0
    current = ""
    for ch in text:
        if ch.isdigit():
            current += ch
            continue
        if ch in _HANGUL_SCALE:
            n = int(current) if current else 1
            total += n * _HANGUL_SCALE[ch]
            current = ""
            continue
        return None  # 모르는 글자가 섞이면 한글 숫자가 아니다
    if current:
        total += int(current)
    return total
```

- [ ] **Step 6: 테스트 통과 확인**

```bash
uv run pytest tests/services/chunithm/test_terms.py -v
```

Expected: PASS (18개)

- [ ] **Step 7: 커밋**

```bash
git add services/chunithm/data/terms.yaml services/chunithm/terms.py \
  migrations/core/009_terms_server.sql tests/services/chunithm/test_terms.py
git commit -m "services: add terms service with Rank/Difficulty/Lamp/Score resolvers"
```

---

## Task 6: songdb 서비스 — 시드 로더 + Chunimaru 덧씌우기 + SongQuery 해석기

**Files:**
- Create: `services/chunithm/songdb.py`
- Create: `tests/services/chunithm/test_songdb.py`

**Interfaces:**
- Consumes: `third_party/chuni_penguin/database/seeds/songs.json`(파일 경로), `services.common.http.HttpService`, `docs/reference/chunimaru/songdb_sample.json`(테스트 전용)
- Produces: `ChartRecord(difficulty: str, const: float | None, note_count: int | None, is_const_unknown: bool)`, `SongRecord(title: str, aliases: tuple[str, ...], artist: str, charts: dict[str, ChartRecord], is_new: bool)`, `SongDbService`(`resolve(raw, ctx) -> SongRecord`[`SongQuery`의 `Resolver` 구현], `const_for(title, difficulty) -> float | None`, `note_count_for(title, difficulty) -> int | None`, `refresh_from_chunimaru(http) -> None`). Task 7(`ChartRef`)과 Task 9(`records`)가 쓴다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/chunithm/test_songdb.py
import json
from pathlib import Path

import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.songdb import SongDbService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})
_CHUNIMARU_SAMPLE = Path("docs/reference/chunimaru/songdb_sample.json")


@pytest.fixture
def songdb() -> SongDbService:
    return SongDbService()


async def test_resolve_by_exact_title(songdb: SongDbService) -> None:
    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.title == "B.B.K.K.B.K.K."
    assert "MAS" in song.charts


async def test_resolve_by_alias_case_insensitive(songdb: SongDbService) -> None:
    song = await songdb.resolve("tentai kansoku", _CTX)
    assert song.title == "天体観測"


async def test_resolve_unknown_raises_not_found(songdb: SongDbService) -> None:
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("이세상에없는곡이름절대", _CTX)


async def test_resolve_ambiguous_raises_with_candidates(songdb: SongDbService) -> None:
    # 로컬 시드에 같은 부분 문자열을 공유하는 곡이 다수 있을 만한 흔한 단어로 검증한다.
    # ("Grab your sword"는 songdb_sample.json에도 있어 여러 경로에서 재사용 가능)
    with pytest.raises(ResolutionError) as exc_info:
        await songdb.resolve("a", _CTX)  # 매우 흔한 글자 -> 후보 다수
    assert exc_info.value.candidates  # 최소 하나 이상의 후보가 들어있어야 한다


async def test_const_for_and_note_count_for(songdb: SongDbService) -> None:
    const = songdb.const_for("B.B.K.K.B.K.K.", "MAS")
    note_count = songdb.note_count_for("B.B.K.K.B.K.K.", "MAS")
    assert const == 12.5
    assert note_count is not None and note_count > 0


async def test_const_for_unknown_chart_is_none(songdb: SongDbService) -> None:
    assert songdb.const_for("B.B.K.K.B.K.K.", "ULT") is None


async def test_before_chunimaru_overlay_removed_song_is_unavailable(songdb: SongDbService) -> None:
    # "天体観測"은 로컬 시드의 available=false, removed=true라 덧씌우기 전에도 안 잡혀야 한다.
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("天体観測", _CTX)


async def test_chunimaru_overlay_marks_songs_available_and_flags_const_unknown(
    songdb: SongDbService,
) -> None:
    chunimaru_data = json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8"))
    songdb._apply_chunimaru(chunimaru_data)  # noqa: SLF001 - 테스트가 머지 로직만 검증

    song = await songdb.resolve("B.B.K.K.B.K.K.", _CTX)
    assert song.charts["EXP"].is_const_unknown is True

    # 이 trimmed 샘플에 없는 "天体観測"은 여전히 국제판 미수록으로 남아야 한다.
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("天体観測", _CTX)


async def test_chunimaru_overlay_marks_new_songs(songdb: SongDbService) -> None:
    chunimaru_data = json.loads(_CHUNIMARU_SAMPLE.read_text(encoding="utf-8"))
    songdb._apply_chunimaru(chunimaru_data)  # noqa: SLF001

    song = await songdb.resolve("Ave Mujica", _CTX)
    assert song.is_new is True


async def test_refresh_from_chunimaru_failure_keeps_previous_state(songdb: SongDbService) -> None:
    class _FailingHttp:
        async def get_json(self, url: str) -> None:
            raise RuntimeError("network down")

    # 실패해도 예외가 새지 않고(기동이 안 죽는다), 이전 상태(seed 기준 availability)가 유지된다.
    await songdb.refresh_from_chunimaru(_FailingHttp())
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await songdb.resolve("天体観測", _CTX)
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/services/chunithm/test_songdb.py -v
```

Expected: FAIL (`services.chunithm.songdb` 없음)

- [ ] **Step 3: 구현**

```python
# services/chunithm/songdb.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from core.resolvers import ResolutionError
from core.tool_types import ToolContext

logger = logging.getLogger(__name__)

_SEED_PATH = (
    Path(__file__).parent.parent.parent
    / "third_party" / "chuni_penguin" / "database" / "seeds" / "songs.json"
)
_MAX_CANDIDATES = 8


class _HttpGetJson(Protocol):
    async def get_json(self, url: str) -> Any: ...


@dataclass(slots=True, kw_only=True)
class ChartRecord:
    difficulty: str
    const: float | None
    note_count: int | None
    is_const_unknown: bool = False


@dataclass(slots=True, kw_only=True)
class SongRecord:
    title: str
    aliases: tuple[str, ...]
    artist: str
    charts: dict[str, ChartRecord] = field(default_factory=dict)
    is_new: bool = False


def _note_count(chart: dict[str, Any]) -> int | None:
    total = sum(chart.get(key, 0) or 0 for key in ("tap", "hold", "slide", "air", "flick"))
    return total if total > 0 else None


def _load_seed(seed_path: Path) -> dict[str, SongRecord]:
    raw = json.loads(seed_path.read_text(encoding="utf-8"))
    songs: dict[str, SongRecord] = {}
    for entry in raw:
        charts = {
            c["difficulty"]: ChartRecord(
                difficulty=c["difficulty"], const=c.get("const"), note_count=_note_count(c)
            )
            for c in entry.get("charts", [])
        }
        songs[entry["title"]] = SongRecord(
            title=entry["title"],
            aliases=tuple(entry.get("aliases") or ()),
            artist=entry.get("artist", ""),
            charts=charts,
        )
    return songs


class SongDbService:
    """곡·채보 정보, 상수, 노트 수, 별칭, 국제판 수록 여부. 시드를 메모리에 한 번
    읽고, Chunimaru songdb로 국제판 가용성·신곡 여부·추정 상수 플래그를 덧씌운다."""

    def __init__(self, seed_path: Path = _SEED_PATH) -> None:
        self._songs = _load_seed(seed_path)
        # Chunimaru가 아직 한 번도 안 덧씌워졌으면 시드 자신의 available 플래그로
        # 근사한다(완벽하지 않지만 기동 중 네트워크 장애에도 안 죽게 하는 하한선이다).
        raw = json.loads(seed_path.read_text(encoding="utf-8"))
        self._available_intl = {e["title"] for e in raw if e.get("available")}

    async def resolve(self, raw: str, ctx: ToolContext) -> SongRecord:
        text = raw.strip()
        available_songs = [s for title, s in self._songs.items() if title in self._available_intl]

        for song in available_songs:
            if song.title == text:
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

    def const_for(self, title: str, difficulty: str) -> float | None:
        song = self._songs.get(title)
        if song is None:
            return None
        chart = song.charts.get(difficulty)
        return chart.const if chart else None

    def note_count_for(self, title: str, difficulty: str) -> int | None:
        song = self._songs.get(title)
        if song is None:
            return None
        chart = song.charts.get(difficulty)
        return chart.note_count if chart else None

    def _apply_chunimaru(self, data: dict[str, Any]) -> None:
        """Chunimaru songdb 응답(이미 디코딩된 dict)을 로컬 시드에 덧씌운다.
        국제판 가용성은 이 목록으로 완전히 교체한다(시드보다 최신이다).
        네트워크 호출은 refresh_from_chunimaru가 하고, 이 메서드는 순수 머지라
        테스트가 저장된 샘플 JSON으로 직접 부른다."""
        new_available: set[str] = set()
        for entry in data.get("songs", []):
            title = entry["title"]
            new_available.add(title)
            song = self._songs.get(title)
            if song is None:
                continue
            song.is_new = bool(entry.get("isNew", False))
            for code, chart_data in entry.get("charts", {}).items():
                existing = song.charts.get(code)
                const = chart_data.get("const")
                is_const_unknown = bool(chart_data.get("isConstUnknown", False))
                if existing is not None:
                    song.charts[code] = replace(
                        existing, const=const, is_const_unknown=is_const_unknown
                    )
                else:
                    song.charts[code] = ChartRecord(
                        difficulty=code, const=const, note_count=None, is_const_unknown=is_const_unknown
                    )
        self._available_intl = new_available

    async def refresh_from_chunimaru(self, http: _HttpGetJson) -> None:
        """기동 시 한 번(또는 이후 단계의 @schedule이) 부른다. 실패해도 예외를 밖으로
        내지 않는다 — 네트워크가 끊겨도 봇 기동 자체는 막지 않고 시드 기준 availability로
        남는다(00-core.md "켜진 직후" 규칙)."""
        try:
            data = await http.get_json("https://chunimaru.fuwamofu.net/api/songdb/intl")
        except Exception:
            logger.warning("Chunimaru songdb 갱신 실패, 이전 상태를 유지한다", exc_info=True)
            return
        self._apply_chunimaru(data)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/chunithm/test_songdb.py -v
```

Expected: PASS (9개)

- [ ] **Step 5: 커밋**

```bash
git add services/chunithm/songdb.py tests/services/chunithm/test_songdb.py
git commit -m "services: add songdb service with seed loader and Chunimaru overlay"
```

---

## Task 7: ChartRef 해석기 — SongQuery + 난이도 합성

**Files:**
- Create: `services/chunithm/chart_ref.py`
- Create: `tests/services/chunithm/test_chart_ref.py`

**Interfaces:**
- Consumes: `services.chunithm.songdb.SongDbService`, `services.chunithm.terms.TermsService`
- Produces: `ResolvedChart(song: SongRecord, chart: ChartRecord)`, `ChartRefResolver`(`resolve(raw, ctx) -> ResolvedChart`, `ChartRef`의 `Resolver` 구현). Task 10이 `ChartRef` 자리에 등록한다.

**설계서 근거**: "ChartRef | 모델이 넘기는 것: SongQuery + 난이도 | 코어가 하는 일: 위와 같고, 그 난이도 채보가 있는지 확인. 난이도가 없으면 MAS"(00-core.md). 즉 모델은 "크로노 마스"처럼 곡 이름과 난이도가 합쳐진 문자열 하나를 통째로 넘기고, 해석기가 그 안에서 난이도 토큰을 떼어낸다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/chunithm/test_chart_ref.py
import pytest

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.chart_ref import ChartRefResolver
from services.chunithm.songdb import SongDbService
from services.chunithm.terms import TermsService

_CTX = ToolContext(player_id="p1", tags=frozenset(), services={})


@pytest.fixture
def resolver() -> ChartRefResolver:
    return ChartRefResolver(songdb=SongDbService(), terms=TermsService())


async def test_difficulty_suffix_is_parsed_out(resolver: ChartRefResolver) -> None:
    resolved = await resolver.resolve("B.B.K.K.B.K.K. 마스", _CTX)
    assert resolved.song.title == "B.B.K.K.B.K.K."
    assert resolved.chart.difficulty == "MAS"


async def test_difficulty_prefix_is_parsed_out(resolver: ChartRefResolver) -> None:
    resolved = await resolver.resolve("마스 B.B.K.K.B.K.K.", _CTX)
    assert resolved.song.title == "B.B.K.K.B.K.K."
    assert resolved.chart.difficulty == "MAS"


async def test_no_difficulty_token_defaults_to_master(resolver: ChartRefResolver) -> None:
    resolved = await resolver.resolve("B.B.K.K.B.K.K.", _CTX)
    assert resolved.chart.difficulty == "MAS"


async def test_missing_difficulty_on_song_raises(resolver: ChartRefResolver) -> None:
    with pytest.raises(ResolutionError, match="그 난이도 없음"):
        await resolver.resolve("B.B.K.K.B.K.K. 울티마", _CTX)


async def test_unknown_song_raises_not_found(resolver: ChartRefResolver) -> None:
    with pytest.raises(ResolutionError, match="찾지 못함"):
        await resolver.resolve("이세상에없는곡절대", _CTX)
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/services/chunithm/test_chart_ref.py -v
```

Expected: FAIL (`services.chunithm.chart_ref` 없음)

- [ ] **Step 3: 구현**

```python
# services/chunithm/chart_ref.py
from __future__ import annotations

from dataclasses import dataclass

from core.resolvers import ResolutionError
from core.tool_types import ToolContext
from services.chunithm.songdb import ChartRecord, SongDbService, SongRecord
from services.chunithm.terms import TermsService

_DEFAULT_DIFFICULTY = "MASTER"
_DEFAULT_DIFFICULTY_CODE = "MAS"

# terms.difficulty_token()이 돌려주는 정식 긴 이름("MASTER")을 로컬 시드가 쓰는
# 짧은 코드("MAS")로 바꾼다 — songdb의 ChartRecord.difficulty는 짧은 코드를 쓴다.
_LONG_TO_SHORT = {
    "BASIC": "BAS", "ADVANCED": "ADV", "EXPERT": "EXP",
    "MASTER": "MAS", "ULTIMA": "ULT", "WORLD'S END": "WE",
}


@dataclass(slots=True, kw_only=True)
class ResolvedChart:
    song: SongRecord
    chart: ChartRecord


class ChartRefResolver:
    """SongQuery + 난이도가 합쳐진 원문 하나(예: "크로노 마스")를 채보로 바꾼다."""

    def __init__(self, *, songdb: SongDbService, terms: TermsService) -> None:
        self._songdb = songdb
        self._terms = terms

    async def resolve(self, raw: str, ctx: ToolContext) -> ResolvedChart:
        tokens = raw.strip().split()
        difficulty_code = _DEFAULT_DIFFICULTY_CODE
        song_tokens = tokens

        if tokens:
            last_token_difficulty = self._terms.difficulty_token(tokens[-1])
            first_token_difficulty = self._terms.difficulty_token(tokens[0])
            if last_token_difficulty is not None:
                difficulty_code = _LONG_TO_SHORT.get(last_token_difficulty, last_token_difficulty)
                song_tokens = tokens[:-1]
            elif first_token_difficulty is not None:
                difficulty_code = _LONG_TO_SHORT.get(first_token_difficulty, first_token_difficulty)
                song_tokens = tokens[1:]

        song_query = " ".join(song_tokens) if song_tokens else raw.strip()
        song = await self._songdb.resolve(song_query, ctx)

        chart = song.charts.get(difficulty_code)
        if chart is None:
            raise ResolutionError(message="그 난이도 없음")
        return ResolvedChart(song=song, chart=chart)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/chunithm/test_chart_ref.py -v
```

Expected: PASS (5개)

- [ ] **Step 5: 커밋**

```bash
git add services/chunithm/chart_ref.py tests/services/chunithm/test_chart_ref.py
git commit -m "services: add ChartRef resolver composing songdb and terms"
```

---

## Task 8: chunithm_net 서비스 — 로그인, 상태, 로그아웃, 만료 감지

**Files:**
- Create: `services/chunithm/chunithm_net.py`
- Create: `tests/services/chunithm/test_chunithm_net.py`

**Interfaces:**
- Consumes: `services.common.secrets.SecretsService`, `services.common.players.PlayersService`(`set_status_tag`/`revoke_status_tag`), `aiosqlite.Connection`, `core.tool_types.ToolContext.emit`
- Produces: `ChunithmNetAdapterLike`(Protocol: `get_profile`, `get_best30`, `get_new20`, `get_recent_scores`, `logout`, `aclose`), `ChunithmNetService`(`login(player_id, clal, ctx) -> Profile`, `status(player_id) -> LoginStatus`, `logout(player_id, ctx) -> None`, `fetch_for_sync(player_id) -> SyncFetch`). 로그인 실패/만료 시 `chunithm.login.expired` 이벤트(알림)를 `ctx.emit`으로 낸다. Task 9가 `fetch_for_sync`가 돌려주는 `SyncFetch`(profile, best30, new20, recent_scores)를 그대로 `records.save_sync()`에 넘긴다.

실제 어댑터(`ChunithmNetAdapter`)는 생성자에서 직접 `httpx.AsyncClient`를 만들어 네트워크로 나가므로, 서비스 테스트는 진짜 어댑터를 쓰지 않고 `adapter_factory`로 주입한 가짜를 쓴다. 가짜의 반환값은 저장된 CHUNITHM-NET 페이지를 vendored 파서로 직접 읽어 만들어서, "파싱 결과가 서비스 로직에 올바르게 전달되는지"는 여전히 실제 데이터로 검증한다.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/services/chunithm/test_chunithm_net.py
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest
from selectolax.lexbor import LexborHTMLParser

from chuni_penguin.adapters.chunithm_net.parser import parse_music_for_rating, parse_player_data
from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.chunithm_net import ChunithmNetService, SyncFetch
from services.common.players import PlayersService
from services.common.secrets import SecretsService

from cryptography.fernet import Fernet

_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
_COOKIES_MIGRATION = Path("migrations/core/007_chunithm_net_cookies.sql").read_text()

_PROFILE_HTML = Path("docs/reference/chunithm-net/player_data.html").read_text(encoding="utf-8")
_BEST_HTML = Path("docs/reference/chunithm-net/rating_detail_best.html").read_text(encoding="utf-8")
_RECENT_HTML = Path("docs/reference/chunithm-net/rating_detail_recent.html").read_text(encoding="utf-8")


def _fixture_profile():
    return parse_player_data(LexborHTMLParser(_PROFILE_HTML))


def _fixture_best30():
    return parse_music_for_rating(LexborHTMLParser(_BEST_HTML))


def _fixture_new20():
    return parse_music_for_rating(LexborHTMLParser(_RECENT_HTML))


class _FakeAdapter:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.logged_out = False

    async def get_profile(self):
        if self.fails:
            raise ConnectionError("쿠키 만료")
        return _fixture_profile()

    async def get_best30(self):
        return _fixture_best30()

    async def get_new20(self):
        return _fixture_new20()

    async def get_recent_scores(self):
        return []

    async def logout(self) -> None:
        self.logged_out = True

    async def aclose(self) -> None:
        pass


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_PLAYERS_MIGRATION + _COOKIES_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def secrets() -> SecretsService:
    return SecretsService(Fernet.generate_key())


def _make_service(conn, secrets, *, adapter: _FakeAdapter, players: PlayersService) -> ChunithmNetService:
    return ChunithmNetService(
        conn=conn, secrets=secrets, players=players, adapter_factory=lambda cookie: adapter
    )


async def test_login_success_stores_cookie_and_grants_linked_tag(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    bus = EventBus()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    profile = await service.login(player.id, "a" * 64, ctx)

    assert profile.username
    assert await players.tags_for(player.id) == {"chunithm-linked"}


async def test_login_failure_does_not_store_cookie_or_grant_tag(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    bus = EventBus()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=bus)
    service = _make_service(conn, secrets, adapter=_FakeAdapter(fails=True), players=players)

    with pytest.raises(ConnectionError):
        await service.login(player.id, "a" * 64, ctx)

    assert await players.tags_for(player.id) == set()


async def test_status_reports_logged_in_after_login(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)
    await service.login(player.id, "a" * 64, ctx)

    status = await service.status(player.id)
    assert status.linked is True
    assert status.nickname


async def test_status_reports_not_linked_before_login(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)

    status = await service.status(player.id)
    assert status.linked is False


async def test_logout_removes_cookie_and_revokes_tag(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    adapter = _FakeAdapter()
    service = _make_service(conn, secrets, adapter=adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    await service.logout(player.id, ctx)

    assert await players.tags_for(player.id) == set()
    assert adapter.logged_out is True
    status = await service.status(player.id)
    assert status.linked is False


async def test_fetch_for_sync_returns_profile_and_lists(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    service = _make_service(conn, secrets, adapter=_FakeAdapter(), players=players)
    await service.login(player.id, "a" * 64, ctx)

    fetch = await service.fetch_for_sync(player.id)

    assert isinstance(fetch, SyncFetch)
    assert len(fetch.best30) == 30
    assert len(fetch.new20) == 20


async def test_fetch_for_sync_on_connection_failure_expires_login(conn, secrets) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={})
    good_adapter = _FakeAdapter()
    service = _make_service(conn, secrets, adapter=good_adapter, players=players)
    await service.login(player.id, "a" * 64, ctx)

    failing = _FakeAdapter(fails=True)
    service_for_sync = _make_service(conn, secrets, adapter=failing, players=players)

    received: list[object] = []
    bus = EventBus()
    bus.register_notify(
        "chunithm.login.expired", owner_skill="test", fn=lambda payload: received.append(payload)
    )
    ctx_with_bus = ToolContext(
        player_id=player.id, tags=frozenset(), services={}, event_bus=bus
    )

    with pytest.raises(ConnectionError):
        await service_for_sync.fetch_for_sync(player.id, ctx=ctx_with_bus)
    await bus.drain()

    assert len(received) == 1
    assert await players.tags_for(player.id) == set()
```

(핸들러 등록에 쓴 `bus.register_notify`/`bus.drain` 시그니처는 3단계 `core/events.py`를 그대로 따른다 — 이미 있는 API라 이 단계에서 새로 안 만든다.)

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/services/chunithm/test_chunithm_net.py -v
```

Expected: FAIL (`services.chunithm.chunithm_net` 없음)

- [ ] **Step 3: 구현**

```python
# services/chunithm/chunithm_net.py
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import aiosqlite

from core.tool_types import ToolContext
from services.common.players import PlayersService
from services.common.secrets import SecretsService

if TYPE_CHECKING:
    from chuni_penguin.types import PersonalBest, Profile, RecentScore


class ChunithmNetAdapterLike(Protocol):
    async def get_profile(self) -> "Profile": ...
    async def get_best30(self) -> "list[PersonalBest]": ...
    async def get_new20(self) -> "list[PersonalBest]": ...
    async def get_recent_scores(self) -> "list[RecentScore]": ...
    async def logout(self) -> None: ...
    async def aclose(self) -> None: ...


AdapterFactory = Callable[[str], ChunithmNetAdapterLike]


def _default_adapter_factory(lwp_cookie_jar: str) -> ChunithmNetAdapterLike:
    from chuni_penguin.adapters.chunithm_net import ChunithmNetAdapter

    return ChunithmNetAdapter(database=None, discord_id=0, lwp_cookie_jar=lwp_cookie_jar)


@dataclass(slots=True, kw_only=True)
class LoginStatus:
    linked: bool
    nickname: str | None = None
    last_checked_at: str | None = None


@dataclass(slots=True, kw_only=True)
class SyncFetch:
    profile: "Profile"
    best30: "list[PersonalBest]"
    new20: "list[PersonalBest]"
    recent_scores: "list[RecentScore]"


class ChunithmNetService:
    """플레이어별로 CHUNITHM-NET 어댑터를 열어 준다. 복호화는 이 서비스 안에서만
    하고, 평문 쿠키는 어댑터를 만드는 그 함수 스코프를 벗어나지 않는다."""

    def __init__(
        self,
        *,
        conn: aiosqlite.Connection,
        secrets: SecretsService,
        players: PlayersService,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row
        self._secrets = secrets
        self._players = players
        self._adapter_factory = adapter_factory or _default_adapter_factory

    async def login(self, player_id: str, clal: str, ctx: ToolContext) -> "Profile":
        """clal 값으로 프로필을 한 번 읽어 본다. 성공해야 저장하고 태그를 붙인다 —
        실패하면 아무것도 바꾸지 않는다(설계서: 실패면 이유를 본인에게만 알린다)."""
        lwp_cookie_jar = f"#LWP-Cookies-2.0\nSet-Cookie3: clal={clal}\n"
        adapter = self._adapter_factory(lwp_cookie_jar)
        try:
            profile = await adapter.get_profile()
        finally:
            await adapter.aclose()

        encrypted = self._secrets.encrypt(clal)
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO chunithm_net_cookies (player_id, encrypted_cookie, linked_at, last_checked_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (player_id) DO UPDATE SET "
            "encrypted_cookie = excluded.encrypted_cookie, linked_at = excluded.linked_at, "
            "last_checked_at = excluded.last_checked_at",
            (player_id, encrypted, now, now),
        )
        await self._conn.commit()
        await self._players.set_status_tag(player_id, "chunithm-linked", source="chunithm_net:login")
        return profile

    async def status(self, player_id: str) -> LoginStatus:
        row = await (
            await self._conn.execute(
                "SELECT last_checked_at FROM chunithm_net_cookies WHERE player_id = ?", (player_id,)
            )
        ).fetchone()
        if row is None:
            return LoginStatus(linked=False)
        tags = await self._players.tags_for(player_id)
        if "chunithm-linked" not in tags:
            return LoginStatus(linked=False)
        return LoginStatus(linked=True, last_checked_at=row["last_checked_at"])

    async def logout(self, player_id: str, ctx: ToolContext) -> None:
        cookie = await self._decrypted_cookie(player_id)
        if cookie is not None:
            adapter = self._adapter_factory(cookie)
            try:
                await adapter.logout()
            finally:
                await adapter.aclose()
        await self._conn.execute(
            "DELETE FROM chunithm_net_cookies WHERE player_id = ?", (player_id,)
        )
        await self._conn.commit()
        await self._players.revoke_status_tag(player_id, "chunithm-linked")

    async def fetch_for_sync(self, player_id: str, *, ctx: ToolContext | None = None) -> SyncFetch:
        """동기화에 필요한 세 가지(프로필, 베스트30, 신곡20, 최근 이력)를 한 번에
        가져온다. 접속이 실패하면 만료로 보고 chunithm-linked를 떼고
        chunithm.login.expired를 낸 뒤 그대로 예외를 다시 던진다 — 호출한 쪽
        (5단계 chunithm-sync 스킬)이 "/로그인 안내" 오류로 바꾼다."""
        cookie = await self._decrypted_cookie(player_id)
        if cookie is None:
            raise ConnectionError("로그인되어 있지 않다")

        adapter = self._adapter_factory(cookie)
        try:
            profile = await adapter.get_profile()
            best30 = await adapter.get_best30()
            new20 = await adapter.get_new20()
            recent_scores = await adapter.get_recent_scores()
        except Exception:
            await self._players.revoke_status_tag(player_id, "chunithm-linked")
            if ctx is not None:
                await ctx.emit("chunithm.login.expired", {"player_id": player_id})
            raise
        finally:
            await adapter.aclose()

        return SyncFetch(profile=profile, best30=best30, new20=new20, recent_scores=recent_scores)

    async def _decrypted_cookie(self, player_id: str) -> str | None:
        """평문은 이 메서드를 부른 호출자 스코프에만 잠깐 존재한다 — 반환값을
        로그나 LLM 입력에 넣지 않는 것은 호출하는 쪽의 책임이다(CLAUDE.md)."""
        row = await (
            await self._conn.execute(
                "SELECT encrypted_cookie FROM chunithm_net_cookies WHERE player_id = ?", (player_id,)
            )
        ).fetchone()
        if row is None:
            return None
        decrypted = self._secrets.decrypt(row["encrypted_cookie"])
        return f"#LWP-Cookies-2.0\nSet-Cookie3: clal={decrypted}\n"
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/services/chunithm/test_chunithm_net.py -v
```

Expected: PASS (7개)

- [ ] **Step 5: 커밋**

```bash
git add services/chunithm/chunithm_net.py tests/services/chunithm/test_chunithm_net.py
git commit -m "services: add chunithm_net service with login, status, logout, expiry detection"
```

---

## Task 9: records 서비스 — save_sync, 세션 배정, 레이팅 대조 (핵심 통과 기준)

**Files:**
- Create: `services/chunithm/records.py`
- Create: `tests/services/chunithm/test_records.py`

**Interfaces:**
- Consumes: `services.chunithm.records_calc.*`(Task 2), `services.chunithm.songdb.SongDbService`(Task 6), `services.common.players.PlayersService`(Task 3), `services.chunithm.chunithm_net.SyncFetch`(Task 8의 타입, import만)
- Produces: `SyncResult(is_first_sync: bool, computed_rating: Decimal, displayed_rating: Decimal | None, rating_mismatch: bool, session_ids: list[str])`, `RecordsService`(`save_sync(player_id, fetch: SyncFetch, ctx) -> SyncResult`). 5단계 이후 스킬이 `ctx.records.save_sync(...)`로 쓴다.

**이 Task의 Step 5가 이번 단계 전체의 핵심 통과 기준이다**: 저장된 CHUNITHM-NET 페이지 3장으로 계산한 전체 레이팅이 그 페이지의 표시 레이팅과 맞아야 한다.

- [ ] **Step 1: 실패하는 테스트 작성(저장소 CRUD 부분)**

```python
# tests/services/chunithm/test_records.py
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import aiosqlite
import pytest
from selectolax.lexbor import LexborHTMLParser

from chuni_penguin.adapters.chunithm_net.parser import parse_music_for_rating, parse_player_data
from core.events import EventBus
from core.tool_types import ToolContext
from services.chunithm.chunithm_net import SyncFetch
from services.chunithm.records import RecordsService
from services.chunithm.songdb import SongDbService
from services.common.players import PlayersService

_PLAYERS_MIGRATION = (
    Path("migrations/core/001_players.sql").read_text()
    + Path("migrations/core/002_player_tags.sql").read_text()
)
_RECORDS_MIGRATION = Path("migrations/core/008_records.sql").read_text()

_PROFILE_HTML = Path("docs/reference/chunithm-net/player_data.html").read_text(encoding="utf-8")
_BEST_HTML = Path("docs/reference/chunithm-net/rating_detail_best.html").read_text(encoding="utf-8")
_RECENT_HTML = Path("docs/reference/chunithm-net/rating_detail_recent.html").read_text(encoding="utf-8")


@pytest.fixture
async def conn() -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(":memory:")
    await connection.executescript(_PLAYERS_MIGRATION + _RECORDS_MIGRATION)
    await connection.commit()
    try:
        yield connection
    finally:
        await connection.close()


def _fixture_fetch() -> SyncFetch:
    profile = parse_player_data(LexborHTMLParser(_PROFILE_HTML))
    best30 = parse_music_for_rating(LexborHTMLParser(_BEST_HTML))
    new20 = parse_music_for_rating(LexborHTMLParser(_RECENT_HTML))
    return SyncFetch(profile=profile, best30=best30, new20=new20, recent_scores=[])


async def test_first_sync_sets_baseline_and_grants_synced_tag(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.is_first_sync is True
    assert await players.tags_for(player.id) == {"chunithm-synced"}


async def test_second_sync_is_not_first(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)
    await service.save_sync(player.id, _fixture_fetch(), ctx)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.is_first_sync is False


async def test_save_sync_upserts_personal_bests(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM records_pb WHERE player_id = ?", (player.id,)
    )
    row = await cursor.fetchone()
    assert row[0] == 50  # 베스트30 + 신곡20, 중복 없음(제목이 전부 다른 저장본 기준)


async def test_save_sync_logs_sync_entry(conn) -> None:
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    await service.save_sync(player.id, _fixture_fetch(), ctx)

    cursor = await conn.execute(
        "SELECT COUNT(*) FROM records_sync_log WHERE player_id = ?", (player.id,)
    )
    row = await cursor.fetchone()
    assert row[0] == 1
```

- [ ] **Step 2: 실패 확인**

```bash
uv run pytest tests/services/chunithm/test_records.py -v
```

Expected: FAIL (`services.chunithm.records` 없음)

- [ ] **Step 3: 구현**

```python
# services/chunithm/records.py
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import aiosqlite

from core.tool_types import ToolContext
from services.chunithm.records_calc import average_rating, assign_session_date, chart_rating
from services.chunithm.songdb import SongDbService
from services.common.players import PlayersService

if TYPE_CHECKING:
    from services.chunithm.chunithm_net import SyncFetch

_GAME = "chunithm"


@dataclass(slots=True, kw_only=True)
class SyncResult:
    is_first_sync: bool
    computed_rating: Decimal
    displayed_rating: Decimal | None
    rating_mismatch: bool
    session_ids: list[str]


class RecordsService:
    """기록 스냅샷, 베스트, 세션 id, 레이팅 계산과 CHUNITHM-NET 대조."""

    def __init__(self, *, conn: aiosqlite.Connection, songdb: SongDbService, players: PlayersService) -> None:
        self._conn = conn
        self._conn.row_factory = aiosqlite.Row
        self._songdb = songdb
        self._players = players

    async def save_sync(self, player_id: str, fetch: "SyncFetch", ctx: ToolContext) -> SyncResult:
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM records_sync_log WHERE player_id = ? AND game = ?",
            (player_id, _GAME),
        )
        row = await cursor.fetchone()
        is_first_sync = row[0] == 0

        synced_at = datetime.now(UTC)
        session_ids: list[str] = []

        all_records = list(fetch.best30) + list(fetch.new20)
        ratings: list[Decimal] = []
        for pb in all_records:
            title = pb.song.title
            difficulty = pb.chart.difficulty.name
            difficulty_code = _SHORT_CODE[difficulty] if difficulty in _SHORT_CODE else difficulty
            const = self._songdb.const_for(title, difficulty_code)
            rating = chart_rating(pb.score, const)
            ratings.append(rating)

            session_id = await self._assign_session(player_id, synced_at)
            if session_id not in session_ids:
                session_ids.append(session_id)

            await self._conn.execute(
                "INSERT INTO records_pb (player_id, game, song_title, difficulty, score, rank, "
                "clear_lamp, combo_lamp, chain_lamp, const, rating, session_id, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (player_id, game, song_title, difficulty) DO UPDATE SET "
                "score = excluded.score, rank = excluded.rank, clear_lamp = excluded.clear_lamp, "
                "combo_lamp = excluded.combo_lamp, chain_lamp = excluded.chain_lamp, "
                "const = excluded.const, rating = excluded.rating, session_id = excluded.session_id, "
                "updated_at = excluded.updated_at",
                (
                    player_id, _GAME, title, difficulty_code, pb.score, str(pb.rank),
                    str(pb.clear_lamp), str(pb.combo_lamp), str(pb.chain_lamp),
                    const, str(rating), session_id, synced_at.isoformat(),
                ),
            )

        computed_rating = average_rating(ratings)  # 이미 둘째 자리에서 버린 값(records_calc.py)
        displayed_rating = None
        if fetch.profile.rating_systems:
            displayed_rating = Decimal(str(fetch.profile.rating_systems[0].value))
        rating_mismatch = displayed_rating is not None and computed_rating != displayed_rating

        await self._conn.execute(
            "INSERT INTO records_sync_log (id, player_id, game, synced_at, nickname, "
            "displayed_rating, computed_rating, rating_mismatch, is_first_sync) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), player_id, _GAME, synced_at.isoformat(), fetch.profile.username,
                str(displayed_rating) if displayed_rating is not None else None,
                str(computed_rating), int(rating_mismatch), int(is_first_sync),
            ),
        )
        await self._conn.commit()

        if is_first_sync:
            await self._players.set_status_tag(player_id, "chunithm-synced", source="records:first_sync")

        return SyncResult(
            is_first_sync=is_first_sync,
            computed_rating=computed_rating,
            displayed_rating=displayed_rating,
            rating_mismatch=rating_mismatch,
            session_ids=session_ids,
        )

    async def _assign_session(self, player_id: str, played_at: datetime) -> str:
        play_date = assign_session_date(played_at)
        cursor = await self._conn.execute(
            "SELECT id FROM records_sessions WHERE player_id = ? AND game = ? AND play_date = ?",
            (player_id, _GAME, play_date.isoformat()),
        )
        row = await cursor.fetchone()
        if row is not None:
            return row["id"]

        session_id = str(uuid.uuid4())
        await self._conn.execute(
            "INSERT INTO records_sessions (id, player_id, game, play_date, estimated, created_at) "
            "VALUES (?, ?, ?, ?, 0, ?)",
            (session_id, player_id, _GAME, play_date.isoformat(), datetime.now(UTC).isoformat()),
        )
        return session_id


_SHORT_CODE = {
    "basic": "BAS", "advanced": "ADV", "expert": "EXP",
    "master": "MAS", "ultima": "ULT", "worlds_end": "WE",
}
```

(`pb.chart.difficulty.name`은 vendored `Difficulty` enum의 멤버 이름(`"master"` 등, 소문자)이다 — `_SHORT_CODE` 조회 키도 소문자로 맞춘다. `records_pb`에 이력이 없는 PB는 이번 동기화의 "마지막 날짜"로 추정 배정해야 하지만, best30/new20 자체에는 플레이 시각이 없다 — 이 메서드는 모든 PB를 **동기화 시각 하나**로 세션 배정한다. "이력에 없는 갱신은 그 동기화의 마지막 날짜에 넣으며 추정 표시"는 `get_recent_scores()`로 얻는 개별 타임스탬프가 있는 플레이에 대한 규칙이고, 이번 단계는 `recent_scores`를 세션에 배정하는 기능까지는 만들지 않는다 — best30/new20을 동기화 시각 하나의 세션에 몰아 넣는 것이 지금 범위의 최소 구현이고, `recent_scores` 기반의 날짜별 세션 분할은 5단계 이후 `chunithm-sync`가 호출 패턴을 정할 때 `assign_session_date`를 그대로 재사용해 확장한다.)

- [ ] **Step 4: 저장소 CRUD 테스트 통과 확인**

```bash
uv run pytest tests/services/chunithm/test_records.py -v
```

Expected: PASS (4개)

- [ ] **Step 5: 핵심 통과 기준 — 저장본 레이팅이 표시값과 맞는지 확인하는 테스트 작성**

```python
# tests/services/chunithm/test_records.py 에 추가
async def test_computed_rating_matches_fixture_displayed_rating(conn) -> None:
    """이번 단계 전체의 핵심 통과 기준: 저장본 베스트30+신곡20으로 계산한 전체
    레이팅이 저장본 프로필 페이지의 표시 레이팅(숫자 이미지에서 읽은 값)과 맞아야
    한다. 두 저장본이 같은 시점(같은 로그인 세션)에서 받은 것이어야 이 테스트가
    의미 있다 — docs/reference/chunithm-net/README나 커밋 로그에 저장 시점을
    남겨, 이후 누가 한쪽만 다시 저장해 어긋나면 바로 알아챌 수 있게 한다."""
    players = PlayersService(conn)
    player = await players.create_player()
    ctx = ToolContext(player_id=player.id, tags=frozenset(), services={}, event_bus=EventBus())
    service = RecordsService(conn=conn, songdb=SongDbService(), players=players)

    result = await service.save_sync(player.id, _fixture_fetch(), ctx)

    assert result.displayed_rating is not None
    assert result.rating_mismatch is False, (
        f"계산값 {result.computed_rating} != 표시값 {result.displayed_rating}"
    )
```

- [ ] **Step 6: 테스트 돌려서 실제로 맞는지 확인**

```bash
uv run pytest tests/services/chunithm/test_records.py::test_computed_rating_matches_fixture_displayed_rating -v
```

Expected: PASS. **만약 FAIL하면(계산값과 표시값이 다르면) 코드를 의심하기 전에 먼저 세 저장본이 같은 시점인지 확인한다** — `player_data.html`이 `rating_detail_best.html`/`rating_detail_recent.html`과 다른 로그인 세션에서 저장됐으면 숫자가 어긋나는 게 정상이다(이 계획을 쓰던 중 실제로 한 번 발견한 문제다 — 사용자가 세 파일을 같은 시점에 다시 저장했는지 먼저 확인).

- [ ] **Step 7: 전체 테스트 통과 확인 후 커밋**

```bash
uv run pytest tests/services/chunithm/test_records.py -v
git add services/chunithm/records.py tests/services/chunithm/test_records.py
git commit -m "services: add records service with save_sync and rating cross-check against CHUNITHM-NET"
```

---

## Task 10: core/bot.py 와이어링 + 전체 리그레션

**Files:**
- Modify: `core/bot.py`
- Modify: `tests/core/test_bot_wiring.py`(새 서비스가 `services` 딕셔너리와 해석기 레지스트리에 들어갔는지 확인하는 테스트 추가)

**Interfaces:**
- Consumes: Task 2~9의 모든 서비스/해석기
- Produces: `build_client()`가 `songdb`, `terms`, `chunithm_net`, `records` 네 서비스를 `ToolExecutor.services`에 담고, `SongQuery`, `ChartRef`, `RankRef`, `DifficultyRef`, `LampRef`, `ScoreRef` 여섯 해석기를 `ResolverRegistry`에 등록한다.

- [ ] **Step 1: `core/bot.py` 수정**

```python
# core/bot.py — import 블록에 추가
from core.tool_types import (
    ArcadeRef, ChartRef, DifficultyRef, LampRef, PlayerRef, RankRef, ScoreRef, SongQuery, ToolContext,
)
from services.chunithm.chart_ref import ChartRefResolver
from services.chunithm.chunithm_net import ChunithmNetService
from services.chunithm.records import RecordsService
from services.chunithm.songdb import SongDbService
from services.chunithm.terms import TermsService
```

```python
# core/bot.py — build_client() 안, resolvers.register(ArcadeRef, ...) 다음에 추가
    songdb_service = SongDbService()
    terms_service = TermsService()
    chunithm_net_service = ChunithmNetService(conn=conn, secrets=secrets_service, players=players) \
        if secrets_service is not None else None
    records_service = RecordsService(conn=conn, songdb=songdb_service, players=players)
    chart_ref_resolver = ChartRefResolver(songdb=songdb_service, terms=terms_service)

    resolvers.register(SongQuery, songdb_service)
    resolvers.register(ChartRef, chart_ref_resolver)
    resolvers.register(RankRef, terms_service)
    resolvers.register(DifficultyRef, terms_service)
    resolvers.register(LampRef, terms_service)
    resolvers.register(ScoreRef, terms_service)
```

(`ResolverRegistry.register`는 `resolve(raw, ctx)`를 가진 어떤 객체든 받으므로, `TermsService`를 네 개 타입에 그대로 등록하면 안 된다 — 하나의 객체는 `Resolver` 프로토콜의 `resolve` 메서드 하나만 가지므로, `RankRef`/`DifficultyRef`/`LampRef`/`ScoreRef` 넷을 같은 `terms_service` 인스턴스에 등록하면 전부 같은 `resolve()`를 타 버린다. 작은 어댑터 네 개로 감싸야 한다 — Step 2에서 바로잡는다.)

- [ ] **Step 2: 해석기 프로토콜 충돌을 작은 어댑터로 바로잡기**

`ResolverRegistry`는 타입 하나에 "`resolve(raw, ctx)`를 가진 객체 하나"만 저장한다. `TermsService`는 `resolve_rank`/`resolve_difficulty`/`resolve_lamp`/`resolve_score` 네 개의 **다른 이름의** 메서드를 갖고 있어(Task 5에서 그렇게 설계했다) 이름 충돌이 없다 — 하지만 `ResolverRegistry.register(ref_type, resolver)`에 넘기는 `resolver`는 `.resolve(raw, ctx)`라는 **하나의 고정된 이름**을 호출받는다(`core/executor.py`의 `resolver.resolve(raw_value, ctx)`). 그래서 `TermsService` 자신을 네 번 등록할 수 없고, 각 값 타입마다 `.resolve`가 해당 메서드로 위임하는 얇은 어�스터가 필요하다.

```python
# core/bot.py — ChartRefResolver 임포트 줄 근처에 작은 어댑터 클래스 네 개 추가
@dataclass(slots=True, kw_only=True)
class _RankRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_rank(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _DifficultyRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_difficulty(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _LampRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_lamp(raw, ctx)


@dataclass(slots=True, kw_only=True)
class _ScoreRefAdapter:
    terms: TermsService

    async def resolve(self, raw: str, ctx: ToolContext):
        return await self.terms.resolve_score(raw, ctx)
```

(`from dataclasses import dataclass`는 이미 `core/bot.py` 상단에 없으므로 import 블록에 추가한다.)

Step 1의 네 줄을 아래로 바꾼다:

```python
    resolvers.register(RankRef, _RankRefAdapter(terms=terms_service))
    resolvers.register(DifficultyRef, _DifficultyRefAdapter(terms=terms_service))
    resolvers.register(LampRef, _LampRefAdapter(terms=terms_service))
    resolvers.register(ScoreRef, _ScoreRefAdapter(terms=terms_service))
```

- [ ] **Step 3: `services` 딕셔너리에 추가하고 Chunimaru 기동 시 덧씌우기 호출**

```python
# core/bot.py — services["arcades"] = arcades_service 다음에 추가
    services["songdb"] = songdb_service
    services["terms"] = terms_service
    services["records"] = records_service
    if chunithm_net_service is not None:
        services["chunithm_net"] = chunithm_net_service
```

```python
# core/bot.py — http_service를 만든 바로 다음(아래 "켜진 직후: ... 곡 목록을 한 번
# 확인한다"는 00-core.md의 새벽 정지 시간 규칙을 그대로 구현하는 자리다)
    await songdb_service.refresh_from_chunimaru(http_service)
```

(`refresh_from_chunimaru`는 Task 6에서 이미 실패를 삼키고 로그만 남기도록 만들었으므로, 네트워크가 안 되는 기동에서도 `build_client()`가 죽지 않는다.)

- [ ] **Step 4: 와이어링 테스트 작성**

```python
# tests/core/test_bot_wiring.py 에 추가
async def test_build_client_registers_chunithm_services_and_resolvers(tmp_path, monkeypatch) -> None:
    # 기존 test_bot_wiring.py의 build_client() 호출 패턴(환경변수/CoreConfig 셋업)을
    # 그대로 따른다 — 이 파일에 이미 있는 셋업 헬퍼를 재사용한다.
    config = _make_test_config(tmp_path)  # 기존 헬퍼 재사용(이미 파일에 있음)
    client, registry, executor = await build_client(config)
    try:
        assert "songdb" in executor.services
        assert "terms" in executor.services
        assert "records" in executor.services
        for ref_type_name in ("SongQuery", "ChartRef", "RankRef", "DifficultyRef", "LampRef", "ScoreRef"):
            ref_type = getattr(__import__("core.tool_types", fromlist=[ref_type_name]), ref_type_name)
            assert executor._resolvers.is_registered(ref_type)  # noqa: SLF001 - 와이어링 확인용
    finally:
        await client.close()
```

(`_make_test_config`가 실제 파일에 이미 있는 이름이 아니면, 그 파일에서 기존 테스트가 `CoreConfig`를 어떻게 만드는지 그대로 베껴 쓴다 — 3단계 때부터 있던 헬퍼 패턴을 새로 발명하지 않는다.)

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/core/test_bot_wiring.py -v
```

Expected: PASS

- [ ] **Step 6: 전체 리그레션**

```bash
uv run pytest
uv run ruff check .
uv run basedpyright
```

Expected: 전부 깨끗. 실패가 있으면 이 Task를 끝내기 전에 고친다(다음 Task가 없으므로 이번 단계의 마지막 안전망이다).

- [ ] **Step 7: 커밋**

```bash
git add core/bot.py tests/core/test_bot_wiring.py
git commit -m "core: wire chunithm_net/songdb/records/terms services and six standard-type resolvers"
```

---

## 완료 후 보고할 것

이 계획이 끝나면 아래를 실제로 돌려 확인한 결과를 보고한다(CLAUDE.md "작업 방식": "그 단계의 통과 기준을 실제로 확인한 결과를 보고한다"):

1. `uv run pytest` 전체 통과 (third_party 패치 포함 vendoring 테스트, records_calc/terms/songdb/chart_ref/chunithm_net/records 테스트, bot wiring 테스트)
2. `uv run ruff check .`, `uv run basedpyright` 깨끗
3. `test_computed_rating_matches_fixture_displayed_rating`이 실제로 통과하는지(저장본 간 시점이 안 맞아 실패했다면 그 사실과 재저장 필요 여부를 보고)
4. 여섯 표준 타입 해석기가 각각 별칭/후보 여럿/찾지 못함/국제판 미수록을 어떤 테스트로 커버했는지 한 줄씩
