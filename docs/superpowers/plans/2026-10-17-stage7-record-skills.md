# 7단계: 기록 스킬 (chunithm-sync, chunithm-report, chunithm-goals, chunithm-notes, chunithm-session) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 동기화(chunithm-sync)→리포트(chunithm-report)→목표(chunithm-goals)/메모(chunithm-notes)→세션 보관(chunithm-session)으로 이어지는 기록 스킬 다섯 개를 설계서(00-core.md, 각 스킬 문서)대로 만든다.

**Architecture:** `records`/`chunithm_net`/`songdb` 서비스(4단계)를 확장해 동기화 한 번의 "변화량 데이터"를 계산·보관하고, `chunithm-sync`가 이를 저장한 뒤 `chunithm.sync.completed` 모으기 이벤트로 리포트·목표 조각을 모아 하나의 리포트로 합치고 `chunithm.sync.reported`를 낸다. `chunithm-session`이 그 알림을 받아 그대로 보관한다. 다섯 스킬은 서로 직접 부르지 않고 이벤트와 서비스로만 잇는다.

**Tech Stack:** Python 3.14, aiosqlite + SQLite(직접 SQL), msgspec(도구 인자 스키마), chuni_penguin(third_party) 어댑터, pytest, discord.py.

**Spec:**

- `docs/design/00-core.md`
- `docs/design/skills/chunithm-sync.md`
- `docs/design/skills/chunithm-report.md`
- `docs/design/skills/chunithm-goals.md`
- `docs/design/skills/chunithm-notes.md`
- `docs/design/skills/chunithm-session.md`

## 결정 사항

2~6단계 코드(특히 `services/chunithm/records.py`, `services/chunithm/chunithm_net.py`, `third_party/chuni_penguin/adapters/chunithm_net/*`, `core/agent.py`, `core/bot.py`, `core/events.py`)를 직접 읽고 확인한 뒤, 이 계획을 세우며 아래를 정했다. 설계서에 없거나 설계서와 미묘하게 다른 부분은 전부 여기 있다. 구현 세션은 이 절만 보고 왜 이렇게 했는지 알 수 있어야 한다.

**A. 이벤트 내용 형식 — msgspec 구조체가 아니라 평문 dict를 그대로 쓴다.**
00-core.md는 "내용 형식은 msgspec 구조체로 정한다"고 적었지만, 실제 코드(`skills/chunithm-song/skill.py`의 `chunithm.song.viewed` 발행, `core/bot.py`의 `players.first_seen`, `services/chunithm/chunithm_net.py`의 `chunithm.login.expired`)는 전부 평문 `dict`를 쓴다. `EventBus`도 `payload: Any`만 받는다. 기존 코드와 다른 형식을 새로 들이면 받는 쪽이 두 가지 형식을 다 다뤄야 하므로, 이번 다섯 스킬이 내는 이벤트도 **평문 dict**로 통일한다. 아래 "이벤트 내용" 절에 각 이벤트의 key를 정확히 적어 이것이 구조체 대신 역할을 하게 한다.

**B. `records` 서비스가 반드시 넓어져야 한다 — 이것부터 한다(Part 1의 Task 1\~4).**
4단계가 만든 `RecordsService.save_sync()`는 베스트30/신곡20만 저장하고, 세션 날짜는 전부 `datetime.now()`(동기화 실행 시각) 하나로 찍는다. 그런데:

- `chuni_penguin.adapters.chunithm_net.parser.parse_music_for_rating`(베스트30/신곡20이 쓰는 파서)는 `achieved_at`을 **절대 채우지 않는다**. 플레이 날짜는 오직 `parse_basic_recent_record`(최근 플레이)만 채운다. 그래서 "여러 날이 섞임 → 날짜로 세션을 나눈다"(chunithm-sync.md)를 지키려면 최근 플레이 이력의 `achieved_at`으로 세션을 배정해야 한다. 지금 코드는 이걸 안 한다.
- "변화 없음"(새 플레이 없음) 판정도 지금 코드엔 없다. PB 행이 실제로 갱신됐는지도 안 돌려준다.
- `chunithm-report`의 "갱신 목록"(점수·랭크·곡 레이팅 변화, 프레임 진입·이탈)과 `chunithm-goals`의 "미갱신"(플레이했지만 최고 기록 유지)을 만들려면, 동기화 하나가 만든 "이전값 대 이후값" 비교 데이터가 필요하다. 설계서(00-core.md 이벤트 규칙)는 "변화량 자체는 이벤트에 넣지 않는다. 받는 스킬이 동기화 id로 records에서 읽는다"고 이미 정해 뒀다 — 그래서 `RecordsService`에 동기화 id로 그 비교 데이터를 돌려주는 창구(`sync_diff`)가 있어야 한다. 지금은 없다.
- **To-Do 자동 완료 범위 질문(아래 "C")의 답에 따라** `records_pb`가 베스트30/신곡20 말고 "플레이어가 플레이한 모든 채보"까지 담아야 한다. 지금은 베스트30/신곡20에 오른 채보만 담는다.

그래서 Part 1은 skill.py를 쓰기 전에 **서비스 확장 작업부터** 한다(Task 1\~4). 이건 "코어/다른 스킬을 고쳐야 한다면 규격이 모자르다는 뜻"(CLAUDE.md)과 같은 상황이지만, `records`/`chunithm_net`는 코어가 아니라 **서비스**이고, 서비스는 "두 개 이상의 스킬이 쓰거나 일관성을 지켜야 하면" 넓히는 게 정상 경로다(00-core.md "서비스" 절). 스킬 다섯 개의 manifest.services 목록은 설계서 그대로 유지한다(바뀌는 건 서비스 내부 구현과 공개 메서드뿐).

**C. To-Do 자동 완료는 동기화마다 난이도별 전체 목록까지 불러온다(사용자 결정, 질문 3).**
`records_pb`는 지금 베스트30·신곡20에 오른 채보만 담는다. 곡별 목표(To-Do)는 임의의 채보에 걸 수 있으므로, 자동 완료를 하려면 "그 채보의 현재 점수"를 알아야 한다. `ChunithmNetAdapter.get_personal_bests(difficulty=...)`는 난이도 하나당 전체 목록을 한 번에 돌려준다(장르 전체="99" 고정 조회) — BAS/ADV/EXP/MAS/ULT/WE 6번이면 플레이어의 전 채보 PB를 담을 수 있다. `chunithm-sync.run()`이 동기화마다 이 6회를 추가로 불러와 `records.save_sync()`에 같이 넘긴다(기존 프로필+베스트30+신곡20+최근 이력 4회와 합쳐 동기화 1회당 CHUNITHM-NET 요청 최대 10회). 그래서 `records_pb`는 이제 "베스트30·신곡20에 오른 채보"가 아니라 "플레이어가 CHUNITHM-NET에 PB를 가진 모든 채보"를 담는다. `chunithm-goals`의 manifest.services는 그대로 `[records, songdb, notify, players, yuni]`로 두고(chunithm_net을 추가하지 않는다) — 전체 PB는 `chunithm-sync`가 `chunithm_net`으로 가져와 `records`에 저장하고, `chunithm-goals`는 `records`를 통해서만 읽는다(스킬 규칙 그대로 지켜짐).

**D. "최근 플레이"의 판정 수는 최근 N개만 상세 조회한다(사용자 결정, 질문 1). N=20로 고정.**
`get_recent_scores()`(최근 플레이 목록)는 판정 수(저스티스/어택/미스)를 안 담는다. 트랙 하나씩 `get_detailed_recent_score()`로 다시 불러야 담긴다(트랙당 요청 1회). `chunithm-report.recent`는 받은 목록 중 **앞의 20개만** 상세 조회해 판정 수를 붙이고, 나머지는 점수·랭크만 보여준다. 20는 설정값이 아니라 코드 상수다(바꿀 이유가 생기면 그때 설정으로 올린다).

**E. "분석"의 "판정 수 기반 어택·미스 비중"은 7단계에서 뺀다(사용자 결정, 질문 2 재검토).**
베스트30/신곡20 PB는 `song.id=0`(자리값)으로만 와서 채보별 상세 조회(`get_detailed_recent_score`/`get_personal_bests_on_song`) 경로를 아예 쓸 수 없다(상세 조회는 "최근 플레이 특정 항목"에만 가능하고, 둘 다 실제 CHUNITHM-NET song id가 있어야 한다). 그래서 `analyze` 도구는 **상수대별 평균 점수와 랭크 분포만** 낸다. `docs/design/skills/chunithm-report.md`의 "분석" 설명에서 "판정 수 기반 어택·미스 비중"을 뺀다(Task 6의 마지막 스텝에서 문서를 고친다).

**F. 세션 날짜 배정과 "새 플레이 여부" 판정 — 최근 플레이 이력을 1차 근거로 쓴다.**
`fetch.recent_scores`의 `achieved_at`으로 (제목, 난이도) → 플레이 시각을 모은다. 직전 동기화 시각(`records_sync_log.synced_at`, 이미 저장돼 있음)보다 **나중**인 것만 "이번에 새로 생긴 플레이"(`new_recent`)로 본다. `new_recent`가 비어 있으면 "변화 없음"이다(`has_new_plays=False`). `new_recent`의 날짜들로 `records_sessions`를 만든다(기존 `assign_session_date`/`_assign_session` 그대로 재사용, 날짜 기준만 `datetime.now()`에서 각 플레이의 `achieved_at`으로 바꾼다).
PB가 갱신된 채보는 `new_recent`에서 같은 (제목, 난이도)를 찾아 그 세션에 붙인다(`estimated=False`). 못 찾으면(최근 플레이 이력의 보관 개수 한도를 넘겨 떨어져 나간 경우 — chunithm-sync.md "미확인 사항"이 이미 알고 있는 한계다) 이번 동기화에서 만든 세션 중 가장 늦은 날짜에 붙이고 `estimated=True`로 표시한다. 세션이 하나도 없는데(즉 `has_new_plays=False`) PB가 갱신되는 경우는 이론상 없지만(플레이 없이 점수가 오를 수 없다), 방어적으로 오늘 날짜(로컬) 세션을 만들고 `estimated=True`로 표시한다.
`new_recent`에 있지만 PB가 갱신되지 않은 (제목, 난이도)는 "플레이했지만 최고 기록 유지"(`UnchangedPlay`, goals.md 예시의 "미갱신"과 같다)로 따로 모은다.

**G. 프레임 진입·이탈 — 동기화 직전 상위 30/20을 `records_pb`에서 직접 재계산해 비교한다.**
`records_pb`가 이제 전체 PB를 담으므로(결정 C), 동기화를 적용하기 **전에** `records_pb`를 레이팅 내림차순으로 조회해 (신곡이 아닌 채보 상위 30) / (신곡인 채보 상위 20)을 구한다(신곡 여부는 `songdb`의 `SongRecord.is_new`로 판정). 이것이 "이전 프레임"이다. `fetch.best30`/`fetch.new20`(CHUNITHM-NET이 그대로 알려주는 이번 상위 30/20)의 (제목, 난이도) 집합이 "이번 프레임"이다. 이번 프레임에는 있는데 이전 프레임에 없으면 진입, 이전 프레임에는 있는데 이번 프레임에 없으면 이탈이다.

**H. 동기화 비교 데이터("diff")는 메모리에만 둔다 — DB에 저장하지 않는다.**
00-core.md는 "변화량 자체는 이벤트에 넣지 않는다. 받는 스킬이 동기화 id로 records에서 읽는다"고 정했다. 이 "읽기"가 같은 요청 안에서(저장 → 모으기 이벤트 → 받는 스킬이 바로 읽기) 끝나므로, 재시작 사이에 살아야 할 이유가 없다. `RecordsService`에 `_diff_cache: dict[str, SyncDiff]`를 두고, 오래된 것부터 지우며 최대 200개만 유지한다(그 이상 쌓일 일이 없다 — 한 요청 안에서 쓰고 버려진다). `sync_diff(sync_id)`가 캐시에 없으면(이론상 생기면 안 되지만 방어적으로) `None`을 돌려주고, 받는 쪽은 그 조각을 "데이터 없음"으로 비워 둔다.

**I. 동기화 리포트의 공개/비공개 — "본인 요청은 공개 설정과 무관하게 그 자리에서 답한다" 규칙은 자동 리포트에는 적용하지 않는다.**
00-core.md "권한" 절의 "본인이 채널에서 자기 기록을 요청하면 공개 설정과 상관없이 그 채널에 답한다"는 **직접 물은 조회**(예: `/베스트 보여줘`)에 적용되는 규칙이다. `chunithm-sync.run()`의 리포트는 동기화할 때마다 자동으로 나가는 방송성 결과라 chunithm-sync.md가 따로 "본인만 보기"를 적어 뒀다. 그래서:

- `chunithm-report`/`chunithm-session`의 조회 도구(`profile`/`best`/`recent`/`record`/`analyze`/`candidates`/`list`/`get`)는 **자기 자신**을 조회할 때는 공개 설정을 보지 않고 그 자리(채널이든 DM이든 부른 자리)에 그대로 답한다. 남을 조회할 때만 그 사람의 공개 설정을 확인한다.
- `chunithm-sync.run()`만 공개 설정(`sync_report_public`)을 본다.

**J. 동기화 리포트 비공개 처리 — 멘션·슬래시 모두 DM으로 보내고, 부른 자리에는 짧은 확인만 남긴다.**
chunithm-sync.md 원문은 "슬래시는 본인에게만 보이는 메시지, 멘션은 DM"이라 적었지만, `ToolContext`에는 "슬래시로 불렸는지 멘션으로 불렸는지"나 "지금 채널이 어디인지"를 알 방법이 없다(코어가 이 정보를 스킬에 넘기지 않는다 — 멘션 경로는 `Display.ephemeral`을 애초에 무시한다, `core/bot.py` 주석 참고). 코어를 고치는 대신(이 한 가지 기능 때문에 `ToolContext`에 필드를 추가하는 건 범위가 크다), **두 경로 모두 `ctx.notify.dm()`으로 리포트 전문을 직접 보내고**, 도구가 돌려주는 `ToolResult`에는 "DM으로 보냈다"는 짧은 문구만 담는다(슬래시는 `ephemeral=True`로 본인에게만, 멘션은 그 짧은 문구만 채널에 남고 전문은 DM으로 간다). 비공개가 실제로 지켜지는 결과는 같다. `docs/design/skills/chunithm-sync.md`의 "공개 범위" 문장을 이 내용으로 고친다(Task 5의 마지막 스텝).

**K. 레이팅 올릴 후보곡(`candidates`)의 "상승폭" 기준점 — SSS(상수+2.00) 달성을 가정한다(사용자 결정).**
chunithm-report.md는 "프레임에 들어갈 때 레이팅 상승폭이 큰 순"이라고만 적고 기준 점수를 안 정했다(목표 랭크 인자가 없는 도구라 플레이어가 고를 수도 없다). 사용자가 **SSS(상수+2.00)**를 공통 기준점으로 정했다. 상승폭 = (상수+2.00) − (그 채보가 속할 쪽의 현재 프레임 최저 레이팅: 신곡이면 신곡20 최저, 아니면 베스트30 최저). 안 친 채보는 현재 점수 0으로 보고 SSS까지 남은 점수·어택도 함께 보여준다.

**L. 오버파워 — 독자적으로 계산하지 않고 CHUNITHM-NET 표시값을 그대로 저장·사용한다.**
chunithm-goals.md 미확인 사항이 이미 "맞지 않으면 CHUNITHM-NET 표시값을 그대로 쓴다"고 정해 뒀다. `Profile.over_power`(어댑터가 이미 파싱해 주는 값)를 그대로 `records_sync_log`에 저장하고, 오버파워 목표는 이 값만 쓴다. **폴더별 오버파워 퍼센트**는 채보마다 오버파워를 다시 계산해야 해서(상수+점수 기반 공식, CHUNITHM-NET이 폴더별로는 안 보여준다) 이번 단계에서 만들지 않는다 — `chunithm-goals.set()`이 오버파워 종류를 받을 때 "전체" 범위만 허용하고, "폴더별"은 다음 단계 자리로 비워 둔다(설계서에 이미 있는 "지금 단계 밖은 비워 둔다" 원칙).

**M. "폴더 정의"의 장르 조합은 이번 단계에서 뺀다.**
`services/chunithm/songdb.py`의 `SongRecord`/`ChartRecord`는 장르를 전혀 담지 않는다(시드에도 Chunimaru 응답에도 없다). chunithm-goals.md가 정한 폴더 정의 네 가지(표기 레벨/상수 범위/장르/수록 버전) 중 **표기 레벨·상수 범위·수록 버전**만 지원한다. 장르는 songdb가 갖추면 그때 더한다.

**N. 메모 테이블의 "난이도 없음"은 NULL이 아니라 빈 문자열로 저장한다.**
`notes_notes`의 기본키가 (player_id, game, song_title, difficulty)인데 SQLite는 PRIMARY KEY 안의 NULL을 "서로 다른 값"으로 봐서, 난이도 없는 메모를 같은 곡에 두 번 쓰면 겹쳐쓰기가 안 되고 중복 행이 생긴다. `difficulty` 열을 `TEXT NOT NULL DEFAULT ''`로 두고, "곡 전체 메모"는 빈 문자열로 저장한다.

**O. 세션 스킬은 보관·조회만, 후기(review)는 전혀 안 만든다.**
사용자가 이미 준 범위대로 `chunithm-session`은 `list`/`get`/`delete`만 두고 `review_open` 도구와 `session_reviews` 테이블은 만들지 않는다. `get()`의 "후기" 표시 절(디자인 문서의 4번)은 7단계에 데이터가 없으니 건너뛴다. `chunithm.sync.reported`를 받아도 대화 스레드를 열지 않고 그냥 보관만 한다. `chunithm.session.closed`는 7단계에 이 이벤트를 내는 코드가 없으므로 manifest `emits`에 **적지 않는다**(12단계에서 후기 종료 코드가 생기면 그때 추가한다). `emits: [chunithm.session.viewed]`만 적는다.

**P. 목표/목표진행 테이블 — `due_at`/`notify_offsets`는 저장만 하고 알림 발사는 9단계.**
사용자가 이미 준 범위대로 `chunithm-goals`는 기한·알림 시점을 데이터로만 들고, `@schedule` 등록이나 알림 발송 코드는 만들지 않는다.

**Q. `/동기화`의 쿨다운은 설정값으로 둔다 — `chunithm-sync`의 manifest.services에 `settings`를 추가한다.**
chunithm-sync.md는 "동기화 쿨다운 | 서버 | 2분(제안값)"을 이미 서버별 설정으로 정해 뒀다. 설계서 예시 manifest(`services: [chunithm_net, records, yuni, notify, players]`)에는 `settings`가 없었지만, 이건 그 스킬 문서가 설정 표를 적으면서 services 목록에 반영을 안 한 누락이지, services를 늘리면 안 될 구조적 이유가 아니다(00-core.md는 "자기 스킬이 정의한 설정을 읽을 때도 services에 선언해야 한다"고만 요구한다 — `settings`는 공통 서비스라 추가해도 다른 스킬에 영향이 없다). 그래서 `services`에 `settings`를 더해(`[chunithm_net, records, settings, yuni, notify, players]`) `manifest.settings`에 "동기화 쿨다운"(이름, kind=int, default=120, scope=guild)을 선언하고, `ctx.settings.get_value(scope="guild", scope_id=ctx.guild_id, skill="chunithm-sync", name="동기화 쿨다운", default=120)`로 읽는다. 처음에 코드 상수로 축소했던 건 돌아볼 때 불필요한 축소였다 — 설계서가 이미 정한 설정을 그대로 구현하는 데 드는 비용이 "코드 상수로 깎기"보다 작다.

## Global Constraints

- 숫자(점수, 레이팅, 허용 어택, 달성률)는 전부 `records`/`records_calc`의 순수 함수가 계산한다. 유니의 말은 도구 `data`에 없는 숫자를 쓰지 않는다.
- 곡 레이팅은 소수 둘째 자리에서 버림(이미 `records_calc.chart_rating`이 함). 남은 어택도 둘째 자리 버림 + 남은 점수 동반 표시(`remaining_to_target`가 이미 함).
- 노트 수가 없는 채보는 "계산 불가"로 표시한다(`allowed_attacks`/`remaining_to_target`가 이미 `None`을 돌려준다 — 도구는 그 `None`을 "계산 불가" 문자열로 바꿔야 한다).
- 스킬은 manifest.services에 적은 서비스만 쓴다: `chunithm-sync` [chunithm_net, records, settings, yuni, notify, players](결정 사항 Q), `chunithm-report` [records, songdb, players, chunithm_net](Part 1 실행 중 추가 — `recent` 도구가 판정 수를 붙이려면 저장된 데이터가 없어 `chunithm_net.enrich_recent_with_detail`을 그 자리에서 불러야 한다. 설계서 예시 목록이 빠뜨린 것이지 구조적 이유가 아니다, 결정 사항 Q와 같은 종류), `chunithm-goals` [records, songdb, notify, players, yuni], `chunithm-notes` [songdb, players], `chunithm-session` [records, arcades, yuni, notify, players].
- 바꾸는 도구(`mutates`)는 요청한 플레이어 본인의 데이터만 바꾼다(코어가 강제하지 않으므로 스킬 코드가 항상 `ctx.player_id`로 범위를 좁힌다).
- CHUNITHM-NET에 접속하는 도구는 `cost="network"`를 붙인다.
- 테이블 접두사: `goals_`, `notes_`, `session_` (게임 접두사 `chunithm-`를 뺀 스킬 이름).
- 마이그레이션 파일은 한 번 적용하면 고치지 않는다. `migrations/core/`는 013번부터, 각 스킬은 `001_init.sql`부터.

## Review Focus

- 975,000점 미만 채보의 레이팅(`calculate_rating`이 저상수·저점수 구간도 0 밑으로 안 내려가는지) — 경계값(500,000/800,000/900,000/975,000점) 테스트.
- 노트 수를 모르는 채보가 섞인 리포트/To-Do/후보곡에서 "계산 불가"가 숫자 자리에 끼어들지 않고 문자열로만 나오는지.
- 동기화 쿠키가 동기화 도중 만료(인증 실패)되면 `records`에 저장은 하나도 안 되고(기존 `fetch_for_sync`가 저장 전에 예외를 던진다) `/로그인` 안내만 나가는지 — 부분 저장이 섞이지 않는지.
- 비공개로 설정한 플레이어의 기록을 다른 플레이어가 `chunithm-report`/`chunithm-session`으로 조회하면 함수 안으로 들어가기 전에 막히는지(본인 조회와 구분).
- 최근 플레이 이력에 없는(보관 기간을 넘긴) PB 갱신이 `estimated=True`로 표시되고, 리포트/세션 보관 양쪽에서 그 표시가 드러나는지.

---

## Part 1 실행 중 컨트롤러가 추가한 결정 사항

Part 1을 실제로 구현하면서(subagent-driven-development로, 2026-10-17) 이 계획이 못 보고
지나간 지점 몇 개를 발견해 그 자리에서 정했다. 전부 `.superpowers/sdd/2026-10-17-stage7-
record-skills/progress.md`(작업 당시 ledger, 지금은 지워졌을 수 있다)에 근거와 함께
기록했던 것을 여기로 옮긴다 — 이 문서가 이제 그 기록의 유일한 사본이다.

**R1. Task 0(이 계획에 없던 선행 작업) — 이벤트 핸들러에 실제 ToolContext 배선.**
`core/events.py`의 `EventBus.emit_gather`/`emit_notify`는 핸들러를 `handler.fn(payload)`로
인자 하나만 넘겨 불렀는데, `@on` 핸들러는 전부 `(ctx, payload)` 모양이다(실제 이벤트가
발생하면 TypeError가 난다). 5단계 계획서가 이미 "7단계 시작 전에 고쳐야 한다"고 적어
뒀지만 이 계획에는 반영이 안 돼 있었다. 사용자에게 확인해 범위를 "00-core.md 전체 구현"
(ctx 배선 + 스킬 켜짐 검사 + `@on` 태그 조건)으로 정하고 Task 5 앞에 Task 0으로
끼워 넣었다. `@on`에 `tags`/`tags_any` 추가, `EventBus.bind_dispatch(services, tag_provider,
settings)` 늦은 바인딩, 받는 스킬의 manifest.services만 담은 ToolContext로 호출, 스킬
켜짐·태그 게이트. (최종 전체 리뷰에서 "받는 스킬 자체의 manifest.tags"까지는 검사가
빠졌던 걸 한 번 더 찾아 마저 고쳤다 — I-3, 아래 R5.)

**R2. `chunithm-sync.run()`의 `focus` 인자는 `list[ChartRef]`가 아니라 `ChartRef`(단수).**
지금 코어의 인자 스키마(`core/schema.py`)가 표준 인자 타입의 리스트를 지원하지 않는다
(여러 코어 파일을 고쳐야 하는 범위라 Part 1에서는 하지 않는다). `focus` 인자는 단수로
좁히고, 이벤트 페이로드의 `focus` 필드는 나중에 여러 개를 받을 수 있게 리스트 모양
(`[{"song_title","difficulty"}] | None`)을 그대로 두되 지금은 원소 0~1개만 채운다.

**R3. `chunithm-report`의 manifest.services에 `chunithm_net`을 추가.** 위 Global
Constraints 절에 반영(R2 바로 위 서비스 목록 줄 참고). `recent` 도구에 `cost="network"`.

**R4. `RecordsService`에 작은 공개 메서드 두 개를 추가.**
- `SongDbService.is_new_for(title) -> bool`(Task 4, 프레임 재계산에 신곡 여부가 필요한데
  제목만으로 조회하는 공개 경로가 없었다. `const_for`/`note_count_for`와 같은 패턴).
- `RecordsService.current_frames(player_id) -> tuple[list[PbRow], list[PbRow]]`(Task 6,
  `best` 도구가 지금 베스트30/신곡20을 봐야 하는데 그 재구성 로직이 전부 비공개 메서드
  (`_frames`)에만 있었다 — 로직을 스킬 쪽에 다시 구현하면 숫자가 어긋날 위험이 있어
  한 줄짜리 공개 래퍼를 더했다).
- `RecordsService.rating_deltas(player_id)`(Task 6 수정 라운드, 아래 R6)와 그에 딸린
  `records_sync_log.best30_average_snapshot`/`new20_average_snapshot`
  (`migrations/core/014_records_sync_log_snapshot_averages.sql`).

**R5. (최종 전체 리뷰 I-3) 이벤트 등록이 받는 스킬의 manifest-level `tags`/`tags_any`도
검사하도록 마저 고쳤다.** R1(Task 0)은 `@on` 핸들러 자신의 태그만 검사했다 —
00-core.md가 요구하는 "그 스킬의" 태그 조건(스킬 레벨)이 빠져 있었다. 도구 실행기가
도구 레벨과 스킬 레벨 태그를 독립적으로(합치지 않고) 각각 검사하는 것과 같은 방식으로
맞췄다.

**R6. (최종 전체 리뷰 I-1, I-2) `has_new_plays`의 정의와 리포트 숫자 출처를 통일했다.**
- `has_new_plays`는 최근 플레이 이력에 새 항목이 있을 때뿐 아니라, PB가 실제로 올랐을
  때(`chart_updates`가 비어있지 않을 때)도 `True`다(이전에는 최근 이력 기준만 봐서,
  업로드 지연으로 최근 이력에 아직 안 보이는 PB 갱신이 "변화 없음"으로 조용히 숨었다).
- `chunithm-sync`의 동기화 리포트 조각과 `chunithm-report`의 `/프로필`·`/베스트`가 전체
  레이팅·베스트30평균·신곡20평균의 현재·직전 값을 **같은 출처**(`RecordsService.
  rating_deltas`, 동기화 시점에 저장한 CHUNITHM-NET 스냅샷)에서 가져온다(이전에는 리포트
  조각만 `records_pb` 재계산 값을 썼는데, 이게 신곡 판정이 아직 안 된 상황 등에서 스냅샷
  값과 어긋날 수 있었다).

## Part 1: chunithm-sync, chunithm-report

**통과 전제:** Part 1이 끝나야 Part 2를 시작한다(Part 2/3는 `chunithm.sync.completed`를 받는 쪽이라 Part 1의 이벤트 모양이 먼저 고정돼야 한다).

### Task 1: `chunithm_net` 서비스 확장 — 전체 PB와 상세 판정 조회

**Files:**

- Modify: `services/chunithm/chunithm_net.py`
- Test: `tests/services/test_chunithm_net_sync_fetch.py` (새 파일 — 지금까지 이 서비스는 스킬 테스트 안에서만 간접 테스트됐다면 여기서 직접 둔다. 기존 테스트가 있으면 그 파일에 추가)

**Interfaces:**

- Consumes: `chuni_penguin.adapters.chunithm_net.adapter.ChunithmNetAdapter`(기존), `chuni_penguin.types.song.Difficulty`(새로 import).
- Produces: `SyncFetch.all_personal_bests: dict[str, list[PersonalBest]]`(키는 "BAS"/"ADV"/"EXP"/"MAS"/"ULT"/"WE"), `ChunithmNetService.get_recent_scores(player_id) -> list[RecentScore]`, `ChunithmNetService.enrich_recent_with_detail(player_id, scores, *, limit) -> list[RecentScore]`.

- [ ] **Step 1: `SyncFetch`에 `all_personal_bests: dict[str, list[PersonalBest]] = field(default_factory=dict)` 필드 추가.**
- [ ] **Step 2: `fetch_for_sync`가 프로필/베스트30/신곡20/최근 이력을 받은 뒤, `("BAS","ADV","EXP","MAS","ULT","WE")` 순서로 `adapter.get_personal_bests(difficulty=Difficulty(code))`를 호출해 `all_personal_bests[code]`를 채운다.** 이 루프도 같은 `try/except AuthenticationError` 블록 안에 둔다(인증 실패 시 지금과 똑같이 태그를 떼고 `chunithm.login.expired`를 낸 뒤 예외를 다시 던진다 — 부분 저장 없음, Review Focus 3번).
- [ ] **Step 3: 테스트 — 가짜 어댑터(`ChunithmNetAdapterLike` 구현)의 `get_personal_bests`가 difficulty별로 다른 리스트를 돌려주게 하고, `fetch_for_sync`가 `all_personal_bests`에 6개 키를 전부 채우는지 확인.**
- [ ] **Step 4: `get_recent_scores(self, player_id: str) -> list[RecentScore]` 추가.** 쿠키 복호화 → 어댑터 생성 → `get_recent_scores()` 한 번 → `aclose()`. 쿠키 없으면 `ConnectionError`.
- [ ] **Step 5: `enrich_recent_with_detail(self, player_id: str, scores: list[RecentScore], *, limit: int) -> list[RecentScore]` 추가.** 앞 `limit`개만 `adapter.get_detailed_recent_score(score)`로 바꿔 끼우고 나머지는 그대로 돌려준다(원본 리스트는 건드리지 않고 새 리스트를 돌려준다).
- [ ] **Step 6: 테스트 — `enrich_recent_with_detail`이 `limit=2`일 때 앞 2개만 `judgements`가 채워지고 나머지는 `None`으로 남는지.**
- [ ] **Step 7: 커밋.**

### Task 2: 마이그레이션 — 동기화 로그에 오버파워 열 추가

**Files:**

- Create: `migrations/core/013_records_overpower.sql`

**Interfaces:**

- Produces: `records_sync_log.overpower_value REAL`, `records_sync_log.overpower_percentage REAL` (NULL 허용, 기존 행은 NULL로 남는다).

- [ ] **Step 1: 파일 작성.**

```sql
-- migrations/core/013_records_overpower.sql
ALTER TABLE records_sync_log ADD COLUMN overpower_value REAL;
ALTER TABLE records_sync_log ADD COLUMN overpower_percentage REAL;
```

- [ ] **Step 2: `uv run pytest`로 마이그레이션 적용이 기존 테스트(있다면 `tests/core/test_migrations.py` 류)를 안 깨는지 확인.**
- [ ] **Step 3: 커밋.**

### Task 3: `records_calc.py` 순수 함수 — 세션 매칭, 프레임 변화, 후보곡 상승폭

**Files:**

- Modify: `services/chunithm/records_calc.py`
- Test: `tests/services/test_records_calc.py`(기존 파일에 추가, 없으면 새로 만든다 — 이미 `allowed_attacks` 등을 테스트하는 파일이 있을 것이다. 못 찾으면 `skills/chunithm-border/tests/test_chunithm_border.py` 근처를 확인하고 서비스 전용 테스트 디렉터리를 새로 둔다)

**Interfaces:**

- Produces:
  - `match_session_for_chart(chart_key: tuple[str, str], recent_by_chart: dict[tuple[str, str], list[datetime]], sessions_by_date: dict[date, str], latest_session_id: str | None) -> tuple[str | None, bool]` — (session_id, estimated). `recent_by_chart`에 그 채보가 있으면 가장 늦은 `achieved_at`의 날짜로 `sessions_by_date`를 찾아 `(session_id, False)`. 없으면 `(latest_session_id, True)`. `latest_session_id`도 `None`이면 `(None, True)`(호출하는 쪽이 오늘 날짜 세션을 새로 만들어 다시 부른다).
  - `frame_changes(previous: set[tuple[str, str]], current: set[tuple[str, str]]) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]` — (진입, 이탈) = (`current - previous`, `previous - current`).
  - `candidate_upside(const: float, frame_floor: Decimal, *, target_bonus: Decimal = Decimal("2.00")) -> Decimal` — `Decimal(str(const)) + target_bonus - frame_floor`, 음수면 그대로 음수(호출하는 쪽이 0 이하를 걸러낸다). `target_bonus` 기본값 2.00은 SSS 기준(결정 사항 K).

- [ ] **Step 1: 세 함수의 실패 테스트부터 쓴다.** 예:

```python
def test_match_session_for_chart_uses_latest_recent_play():
    recent_by_chart = {("곡A", "MAS"): [datetime(2026, 10, 10, 20, 0), datetime(2026, 10, 11, 1, 0)]}
    sessions_by_date = {date(2026, 10, 10): "s1", date(2026, 10, 11): "s2"}
    session_id, estimated = match_session_for_chart(("곡A", "MAS"), recent_by_chart, sessions_by_date, latest_session_id="s2")
    assert (session_id, estimated) == ("s2", False)

def test_match_session_for_chart_falls_back_to_latest_when_not_in_recent():
    session_id, estimated = match_session_for_chart(("곡B", "MAS"), {}, {}, latest_session_id="s2")
    assert (session_id, estimated) == ("s2", True)

def test_frame_changes_entries_and_exits():
    entered, exited = frame_changes({("A", "MAS")}, {("B", "MAS")})
    assert entered == {("B", "MAS")}
    assert exited == {("A", "MAS")}

def test_candidate_upside_assumes_sss():
    assert candidate_upside(15.0, Decimal("16.50")) == Decimal("0.50")  # 15.0+2.00-16.50
```

- [ ] **Step 2: 테스트 실행 → 실패 확인(`match_session_for_chart`/`frame_changes`/`candidate_upside` 없음).**
- [ ] **Step 3: 세 함수 구현.**
- [ ] **Step 4: 테스트 통과 확인.**
- [ ] **Step 5: 커밋.**

### Task 4: `RecordsService.save_sync` 재작성 — 전체 PB 저장, diff 계산, diff 캐시

**Files:**

- Modify: `services/chunithm/records.py`
- Test: `tests/services/test_records_save_sync.py`(기존 `save_sync` 테스트 파일이 있으면 그 파일을 확장한다)

**Interfaces:**

- Consumes: Task 1의 `SyncFetch.all_personal_bests`, Task 3의 세 함수.
- Produces(새/변경 타입, `services/chunithm/records.py`에 전부 정의):

```python
@dataclass(slots=True, kw_only=True, frozen=True)
class SessionInfo:
    id: str
    play_date: str  # ISO 날짜
    estimated: bool

@dataclass(slots=True, kw_only=True, frozen=True)
class ChartUpdate:
    song_title: str
    difficulty: str
    old_score: int | None
    new_score: int
    old_rank: str | None
    new_rank: str
    old_rating: Decimal | None
    new_rating: Decimal | None
    session_id: str
    estimated_session: bool
    entered_frame: bool

@dataclass(slots=True, kw_only=True, frozen=True)
class UnchangedPlay:
    song_title: str
    difficulty: str
    play_count: int
    current_score: int
    session_id: str

@dataclass(slots=True, kw_only=True, frozen=True)
class FrameExit:
    song_title: str
    difficulty: str
    old_rating: Decimal | None

@dataclass(slots=True, kw_only=True, frozen=True)
class SyncDiff:
    sync_id: str
    sessions: list[SessionInfo]
    chart_updates: list[ChartUpdate]
    unchanged_plays: list[UnchangedPlay]
    frame_exits: list[FrameExit]
    previous_computed_rating: Decimal | None
    previous_best30_average: Decimal | None
    previous_new20_average: Decimal | None

@dataclass(slots=True, kw_only=True)
class SyncResult:
    sync_id: str
    is_first_sync: bool
    has_new_plays: bool
    computed_rating: Decimal | None
    best30_average: Decimal | None
    new20_average: Decimal | None
    displayed_rating: Decimal | None
    rating_mismatch: bool
    overpower_value: float | None
    overpower_percentage: float | None
```

- `async def save_sync(self, player_id: str, fetch: SyncFetch, ctx: ToolContext) -> SyncResult`(시그니처 동일, 동작 재작성).
- `async def sync_diff(self, sync_id: str) -> SyncDiff | None`.
- `async def latest_overpower(self, player_id: str) -> tuple[float | None, float | None] | None` (value, percentage).
- `async def pb_row(self, player_id: str, *, song_title: str, difficulty: str) -> PbRow | None`(아래 `PbRow` 신설, `best_score`는 기존 그대로 유지 — `chunithm-border`가 쓴다).
- `async def all_pb_rows(self, player_id: str) -> list[PbRow]`.
- `async def latest_sync_summary(self, player_id: str) -> SyncLogSummary | None` — **최신 동기화 로그의 `computed_rating`/`nickname`/`last_synced_at`만** 그 로그 행에서 그대로 읽고, `best30_average`/`new20_average`는 로그에 열을 늘리지 않고 **지금 시점 `records_pb`를 다시 레이팅 내림차순으로 훑어** 계산한다(그래서 이 둘은 "그 동기화 시점"이 아니라 "지금" 값이다 — docstring에 남긴다).
- `candidates(self, player_id: str, *, min_const: float | None, max_const: float | None, count: int) -> list[CandidateHit]`(신규, `async`. `PbRow`/`songdb.find_by_const`/Task 3의 `candidate_upside` 사용).

- [ ] **Step 1: `PbRow`, `SyncLogSummary`, `CandidateHit` 데이터클래스를 더한다.**

```python
@dataclass(slots=True, kw_only=True, frozen=True)
class PbRow:
    song_title: str
    difficulty: str
    score: int
    rank: str
    clear_lamp: str
    combo_lamp: str
    chain_lamp: str
    const: float | None
    rating: Decimal | None
    session_id: str | None
    updated_at: str

@dataclass(slots=True, kw_only=True, frozen=True)
class SyncLogSummary:
    nickname: str | None
    computed_rating: Decimal | None
    best30_average: Decimal | None   # 지금 시점 records_pb 기준(위 설명 참고)
    new20_average: Decimal | None    # 지금 시점 records_pb 기준
    last_synced_at: str

@dataclass(slots=True, kw_only=True, frozen=True)
class CandidateHit:
    song_title: str
    difficulty: str
    const: float
    upside: Decimal           # candidate_upside() 결과
    current_score: int        # 안 쳤으면 0
    remaining_score: int | None     # SSS 기준, 노트 수 없으면 None
    remaining_attacks: Decimal | None
```

- [ ] **Step 2: 첫 동기화 테스트부터 쓴다.**

```python
async def test_first_sync_saves_baseline_without_sessions():
    result = await records.save_sync("p1", fetch_with_only_best30_new20, ctx)
    assert result.is_first_sync is True
    assert result.has_new_plays is False
    diff = await records.sync_diff(result.sync_id)
    assert diff is None  # 첫 동기화는 diff를 안 만든다(리포트를 안 내므로)
```

- [ ] **Step 3: 두 번째 동기화, 변화 없음 테스트.**

```python
async def test_second_sync_with_no_recent_plays_reports_no_new_plays():
    await records.save_sync("p1", fetch_baseline, ctx)
    result = await records.save_sync("p1", fetch_same_recent_as_before, ctx)  # recent_scores의 achieved_at이 전부 직전 synced_at 이전
    assert result.has_new_plays is False
    diff = await records.sync_diff(result.sync_id)
    assert diff.chart_updates == [] and diff.sessions == []
```

- [ ] **Step 4: PB 갱신이 최근 이력에서 매칭되는 경우 테스트(세션 날짜가 `achieved_at` 기준인지, `estimated_session=False`인지).**
- [ ] **Step 5: PB 갱신이 최근 이력에 없는(보관 한도 밖) 경우 테스트(`estimated_session=True`, 이번 동기화에서 만든 세션 중 가장 늦은 날짜에 붙는지).**
- [ ] **Step 6: 플레이했지만 갱신 안 된 채보가 `unchanged_plays`에 들어가고 `play_count`가 맞는지 테스트.**
- [ ] **Step 7: 프레임 진입·이탈 테스트(동기화 전 `records_pb`에 31번째로 높은 레이팅 채보를 하나 심어 두고, 이번 베스트30에 없으면 `frame_exits`에 뜨는지 / 새 채보가 베스트30에 들어오면 그 `ChartUpdate.entered_frame=True`인지).**
- [ ] **Step 8: 레이팅 변화량 테스트 — `previous_computed_rating`/`previous_best30_average`/`previous_new20_average`가 직전 동기화 로그·PB 평균과 맞는지.**
- [ ] **Step 9: `all_personal_bests`로 들어온, 베스트30/신곡20에는 없는 채보도 `records_pb`에 저장되는지(To-Do 자동 완료가 쓸 데이터) 테스트.**
- [ ] **Step 10: 오버파워 저장/조회 테스트(`latest_overpower`).**
- [ ] **Step 11: `candidates` 테스트 — 상수 범위 안의 미플레이 채보가 상승폭 내림차순으로, 이미 프레임 안에 있는 채보는 빠지는지.**
- [ ] **Step 12: 구현.** 큰 흐름은 "결정 사항 F/G/H"를 그대로 따른다. 직전 로그 조회 → 새 `sync_id`(uuid4) 발급 → 최근 이력 매칭용 dict 구성 → (첫 동기화면 세션/diff 없이 베스트30+신곡20+전체PB merge만 저장하고 끝) → 아니면 프레임 전/후 집합 계산 → 채보별 upsert(기존 row SELECT → upsert → rowcount로 갱신 여부 판단) → `ChartUpdate`/`UnchangedPlay` 누적 → 세션 생성(Task 3 함수로 session_id 결정, 없으면 그 자리에서 새 세션 insert) → 집계 레이팅 계산(기존 `average_rating` 그대로) → `records_sync_log` insert(오버파워 포함) → `SyncDiff` 조립해 `_diff_cache`에 저장(200개 초과 시 가장 오래된 것 pop) → `SyncResult` 반환.
- [ ] **Step 13: 전체 테스트 통과 확인(`uv run pytest services/chunithm`).**
- [ ] **Step 14: 기존 `chunithm-border` 테스트가 `best_score`/`allowed_attacks`/`remaining_to_target`에 의존하므로 깨지지 않았는지 `uv run pytest skills/chunithm-border` 확인.**
- [ ] **Step 15: 커밋.**

### Task 5: `skills/chunithm-sync` 생성

**Files:**

- Create: `skills/chunithm-sync/SKILL.md`
- Create: `skills/chunithm-sync/skill.py`
- Create: `skills/chunithm-sync/tests/test_chunithm_sync.py`

**Interfaces:**

- Consumes: Task 4의 `ChunithmNetService.fetch_for_sync`, `RecordsService.save_sync`/`sync_diff`, `core.events.Section`, `ctx.event_bus.emit_gather`, `ctx.yuni.line`, `ctx.notify.dm`.
- Produces: `chunithm.sync.completed`(모으기), `chunithm.sync.reported`(알림) — 아래 "이벤트 내용" 그대로.

**이벤트 내용 (결정 사항 A — 평문 dict)**

`chunithm.sync.completed`(모으기 — `ctx.event_bus.emit_gather`로 낸다):

```python
{
    "player_id": str,
    "sync_id": str,
    "sessions": [{"id": str, "play_date": str, "estimated": bool}, ...],
    "has_new_plays": bool,
    "is_first_sync": bool,
    "focus": [{"song_title": str, "difficulty": str}, ...] | None,
    "previous_synced_at": str | None,
    "arcade_id": str | None,
}
```

`chunithm.sync.reported`(알림 — `ctx.emit`):

```python
{
    **(위 chunithm.sync.completed와 같은 키 전부),
    "rendered_text": str,        # 유니 한마디 + 조각 표시 전체를 이어붙인 최종 텍스트
    "sections_data": [dict, ...],# 조각별 data (순서대로)
    "yuni_line": str,
    "sent_location": {"kind": "channel", "channel_id": str} | {"kind": "dm", "discord_id": str},
}
```

- [ ] **Step 1: `_template`을 복사해 폴더를 만들고 manifest를 채운다**(`name`, `description`, `examples`, `category: 기록과 리포트`, `game: chunithm`, `version: 1`, `services: [chunithm_net, records, settings, yuni, notify, players]`(결정 사항 Q — chunithm-sync.md 예시보다 `settings`가 하나 더 있다), `tags: [chunithm-linked]`, `emits: [chunithm.sync.completed, chunithm.sync.reported]`, `settings: [{name: "동기화 쿨다운", kind: int, default: 120, scope: guild, description: "/동기화 재실행을 막는 최소 간격(초)"}]`).
- [ ] **Step 2: 실패하는 테스트부터 — 첫 동기화 응답.**

```python
async def test_run_first_sync_returns_baseline_message_without_report():
    result = await skill.run(ctx_with_fake_services(first_sync=True), focus=None, quiet=False, arcade=None)
    assert "기준점" in (result.display.content or "")
    assert fake_event_bus.gather_calls == []  # 모으기 이벤트를 안 낸다
```

- [ ] **Step 3: 변화 없음 테스트(모으기 이벤트는 내지만 리포트/세션 보관 신호 없음).**
- [ ] **Step 4: 쿠키 만료 테스트 — `fetch_for_sync`가 `AuthenticationError`를 던지면 `/로그인` 안내 오류를 돌려주고 `save_sync`를 아예 안 부르는지.**
- [ ] **Step 5: 공개 리포트 테스트 — `sync_report_public=True`면 `Display.content`에 전체 리포트가 담기고 `ctx.notify.dm`은 안 불리는지.**
- [ ] **Step 6: 비공개 리포트 테스트 — `sync_report_public=False`면 `ctx.notify.dm`이 전문으로 불리고, 반환 `ToolResult.display`는 "DM으로 보냈다"류 짧은 문구 + `ephemeral=True`인지.**
- [ ] **Step 7: `quiet=True` 테스트 — `ctx.yuni.line`을 안 부르고 조각 표시만 잇는지.**
- [ ] **Step 8: `focus` 테스트 — `chunithm.sync.completed` 페이로드의 `focus`에 넘긴 채보가 들어가는지(값 변환만 확인, 받는 쪽 필터링은 Task 6 몫).**
- [ ] **Step 9: 쿨다운 테스트 — `records.last_sync_at`과 `ctx.settings.get_value(..., name="동기화 쿨다운", default=120)`으로 너무 빠른 재호출을 막는지, 설정값을 바꾸면(예: 10초) 그 값을 따르는지.**
- [ ] **Step 10: 구현.**

```python
@tool(slash="동기화", tags=["chunithm-linked"], cost="network")
async def run(
    ctx: ToolContext, focus: list[ChartRef] | None = None, quiet: bool = False, arcade: ArcadeRef | None = None
) -> ToolResult:
    """CHUNITHM-NET에서 최신 기록을 가져와 저장하고, 지난 동기화와의 차이를 리포트로 낸다."""
    ...
```

- 쿨다운 확인: `ctx.records.last_sync_at(ctx.player_id)`와 `ctx.settings.get_value(scope="guild", scope_id=ctx.guild_id, skill="chunithm-sync", name="동기화 쿨다운", default=120)`를 비교해, 간격이 안 지났으면 `ToolResult(error=...)`로 남은 시간을 안내하고 끝낸다(결정 사항 Q).
- `try: fetch = await ctx.chunithm_net.fetch_for_sync(ctx.player_id, ctx=ctx) except ConnectionError/AuthenticationError 계열: return ToolResult(error="로그인이 풀렸어. /로그인으로 다시 연결해 줘.")`.
- `result = await ctx.records.save_sync(ctx.player_id, fetch, ctx)`.
- 첫 동기화면 바로 반환.
- 변화 없음이어도 모으기 이벤트는 낸다(설계서 그대로) — 받는 쪽이 "현재값만" 돌려줄 책임.
- 조각 모으기 → `yuni.line`(quiet가 아니면) → 조각 `display.content`를 순서대로 이어붙여 `rendered_text` 구성 → 공개/비공개 분기(결정 사항 J) → `chunithm.sync.reported` 발행.

- [ ] **Step 11: `docs/design/skills/chunithm-sync.md`의 "공개 범위" 문장을 결정 사항 J대로 고치고, manifest 예시의 `services`에 `settings`를 더한다(결정 사항 Q — "동기화 쿨다운" 설정 항목 자체는 그대로 둔다, 기본값 120초로 확정).**
- [ ] **Step 12: `uv run pytest skills/chunithm-sync` 통과 확인.**
- [ ] **Step 13: 커밋.**

### Task 6: `skills/chunithm-report` 생성

**Files:**

- Create: `skills/chunithm-report/SKILL.md`
- Create: `skills/chunithm-report/skill.py`
- Create: `skills/chunithm-report/tests/test_chunithm_report.py`

**Interfaces:**

- Consumes: Task 4의 `RecordsService.pb_row`/`all_pb_rows`/`latest_sync_summary`/`sync_diff`/`candidates`, `songdb.find_by_const`, `chunithm.sync.completed` 페이로드(Task 5).
- Produces: `chunithm.sync.completed`의 `order=10` 조각.

- [ ] **Step 1: manifest 작성**(`services: [records, songdb, players]`, `tags: [chunithm-synced]`, `listens: [chunithm.sync.completed]`).
- [ ] **Step 2: `profile` 테스트.** 본인 조회는 공개 설정 무관, 남 조회는 `records_public` 확인(결정 사항 I).
- [ ] **Step 3: `best`/`recent`/`record`/`analyze`/`candidates` 각각 실패 테스트 1개 이상.** `recent`는 Task 1의 `enrich_recent_with_detail(limit=20)`을 쓰는지(20개까지만 판정 수가 붙는지) 반드시 확인.
- [ ] **Step 4: `@on("chunithm.sync.completed") -> Section` 테스트.** `has_new_plays=False`면 현재값만 담은 `Section`(세션 요약 없이 "변화 없음" + 현재 레이팅/평균), `True`면 세션 요약 + 갱신 목록 + 반응 단계(`great`/`good`/`close`/`rest` 판정 — chunithm-report.md 표 그대로, 경계값은 Review Focus 대상이 아니라 이미 설계서가 "개인 최고 갱신"/"목표에 조금 못 미침" 같은 질적 기준이라 1차는 "개인 최고 갱신 → great, 그 외 갱신 있음 → good, 갱신 없고 플레이만 → close, 플레이 없음(변화 없음) → 조각 자체를 간단하게"로 구현하고 세부 기준은 실제 데이터로 다음에 조정한다 — 이 축소를 SKILL.md 본문에 한 줄로 적어 둔다).
- [ ] **Step 5: `focus` 필터링 테스트 — 페이로드에 `focus`가 있으면 갱신 목록을 그 채보로만 거르고 세션 요약은 그대로인지.**
- [ ] **Step 6: 구현(6개 도구 + 1개 이벤트 핸들러).**
- [ ] **Step 7: `docs/design/skills/chunithm-report.md`의 "분석" 설명에서 "판정 수 기반 어택·미스 비중"을 지운다(결정 사항 E).**
- [ ] **Step 8: `uv run pytest skills/chunithm-report` 통과 확인.**
- [ ] **Step 9: 커밋.**

### Part 1 통과 기준

- `uv run pytest`, `uv run ruff check .`, `uv run basedpyright` 전부 통과.
- 테스트로 확인: 첫 동기화는 리포트 없이 기준점만, 둘째 동기화부터 세션 요약·갱신 목록·반응 단계가 나오고, 변화 없을 때 "변화 없음"만 나온다.
- 테스트로 확인: 로그인 쿠키 만료 시 `/로그인` 안내만 나가고 아무것도 저장되지 않는다.
- 테스트로 확인: 비공개 설정 플레이어의 동기화 리포트는 DM으로, 본인이 직접 `/프로필`·`/베스트`를 물으면 공개 설정과 무관하게 그 자리에서 답한다.

### 디스코드에서 직접 확인할 순서 (멘션 + 슬래시 두 길)

1. (사전 준비) `/로그인`으로 테스트 계정을 연결한다(이미 4\~5단계에서 만들어진 기능).
2. **슬래시**: `/동기화` 최초 실행 → "기준점을 잡았다"류 응답만 오고 채널에 리포트가 안 나오는지 확인.
3. CHUNITHM-NET에서 곡 하나를 실제로 플레이해 점수를 올린다(또는 테스트 계정 데이터가 이미 바뀌어 있는 걸 기다린다).
4. **슬래시**: `/동기화` 다시 실행 → 세션 요약 + 갱신 목록이 채널에 공개로 나오는지.
5. `/설정`(또는 account-link가 제공하는 공개 설정 경로)으로 "동기화 리포트 공개 범위"를 본인만 보기로 바꾼다.
6. **멘션**: "유니 동기화해 줘" → 채널에는 짧은 확인만 남고, DM으로 전문이 오는지.
7. **멘션**: "유니 동기화해 줘, 조용히"(quiet) → 유니 말 없이 표시물만 오는지.
8. **슬래시**: `/프로필`, `/베스트`, `/최근`, `/분석`, `/후보곡`을 전부 한 번씩 실행해 숫자가 어긋나지 않는지.
9. **멘션**: "내 레이팅 얼마야?", "레이팅 올릴 곡 추천해 줘"로 같은 결과가 말로도 나오는지.
10. 변화 없이 `/동기화`를 한 번 더 실행해 "변화 없음"만 나오는지.

---

## Part 2: chunithm-goals, chunithm-notes

**통과 전제:** Part 1이 끝나야 시작한다(`chunithm.sync.completed` 페이로드와 `records.sync_diff` 모양이 고정된 뒤).

### Task 7: 마이그레이션 — `goals_goals`, `goals_progress`

**Files:**

- Create: `skills/chunithm-goals/migrations/001_init.sql`

- [ ] **Step 1: 파일 작성.**

```sql
-- skills/chunithm-goals/migrations/001_init.sql
CREATE TABLE goals_goals (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    kind TEXT NOT NULL,              -- rating | overpower | folder | song | freeform
    target_ref TEXT NOT NULL,        -- JSON: 폴더 정의 또는 {song_title, difficulty}, freeform이면 설명 텍스트
    target_value REAL,
    baseline_value REAL,
    created_at TEXT NOT NULL,
    due_at TEXT,
    duration_tag TEXT,               -- short | mid | long | NULL
    parent_goal_id TEXT REFERENCES goals_goals(id),
    status TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress | achieved | expired | stopped
    public INTEGER NOT NULL DEFAULT 0,
    notify_offsets TEXT               -- JSON 배열(일 단위), 저장만 함(발사는 9단계)
);

CREATE TABLE goals_progress (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL REFERENCES goals_goals(id),
    sync_id TEXT,
    recorded_at TEXT NOT NULL,
    value REAL,
    achievement_ratio REAL
);
```

- [ ] **Step 2: 커밋(스킬 폴더가 아직 없으니 이 Task는 Task 9에서 skill.py와 함께 커밋해도 된다 — 먼저 파일만 만들어 두고 다음 Task로 넘어간다).**

### Task 8: `goals_calc.py` 순수 함수 — 달성률, 페이스, 필요량, 프레임 재구성

**Files:**

- Create: `skills/chunithm-goals/goals_calc.py`
- Create: `skills/chunithm-goals/tests/test_goals_calc.py`

**Interfaces:**

- Produces:
  - `achievement_ratio(current: Decimal, baseline: Decimal, target: Decimal) -> Decimal` — `(current-baseline)/(target-baseline)`, `target==baseline`이면 완료 여부로 1 또는 0.
  - `pace(ratio: Decimal, elapsed_ratio: Decimal) -> Literal["ahead", "on_track", "behind"]`.
  - `rating_gap_to_song_rating_sum(remaining_rating: Decimal) -> Decimal` — `remaining_rating * 50`.
  - `project_rating_with_todos(current_best30: list[Decimal], current_new20: list[Decimal], todo_targets: list[tuple[bool, Decimal]]) -> Decimal` — `todo_targets`는 (is_new, 목표 달성 시 곡 레이팅) 목록. 각 todo를 해당 쪽(베스트30이면 상수 상위 30, 신곡20이면 상위 20) 리스트에 끼워 넣고 다시 정렬해 상위 N개만 평균 낸 뒤 50으로 나눈 값을 돌려준다(chunithm-goals.md "프레임을 다시 짜서 계산").
  - `folder_progress(total: int, matched: int) -> Decimal` — `matched/total`, `total==0`이면 "계산 불가" 신호로 `None`.

- [ ] **Step 1: 실패 테스트 — 달성률 경계값(기준값=목표값, 현재값이 기준보다 낮음(음수 허용), 달성 초과(1 넘음 허용인지 — 설계서는 자르라는 말이 없으므로 넘겨도 그대로 둔다)).**
- [ ] **Step 2: 페이스 테스트 — 경과율보다 달성률이 높으면 ahead, 같으면 on_track(허용 오차 없이 완전 일치는 흔치 않으니 ±0.0 비교 그대로, 실제 동작에서 almost-equal 문제가 보이면 다음 리뷰에서 허용폭을 추가한다), 낮으면 behind.**
- [ ] **Step 3: `project_rating_with_todos` 테스트 — 투두 달성 시 베스트30 밖이던 곡이 들어오면서 평균이 오르는 케이스, 이미 프레임 안인 곡의 점수만 오르는 케이스.**
- [ ] **Step 4: `folder_progress` 테스트 — 전체 0이면 None.**
- [ ] **Step 5: 구현.**
- [ ] **Step 6: 커밋(Task 7의 마이그레이션 파일과 함께).**

### Task 9: `skills/chunithm-goals` 생성 — 목표 CRUD, 동기화 점검/자동 완료

**Files:**

- Create: `skills/chunithm-goals/SKILL.md`
- Create: `skills/chunithm-goals/models.py` (SQL 쿼리 모음 — `insert_goal`, `fetch_goal`, `list_goals_for_player`, `update_goal`, `insert_progress`, `delete_goals` 등)
- Create: `skills/chunithm-goals/skill.py`
- Create: `skills/chunithm-goals/tests/test_chunithm_goals.py`

**Interfaces:**

- Consumes: Task 4(`RecordsService.all_pb_rows`/`pb_row`/`sync_diff`/`latest_overpower`), Task 8의 네 함수, `chunithm.sync.completed`(Task 5), `chunithm.song.viewed`(기존 `chunithm-song` 발행분, payload `{"player_id","song_title","difficulty"}`), `chunithm.plan.requested`(아직 내는 스킬 없음 — 테스트에서 직접 emit).
- Produces: `chunithm.goal.completed`(알림), `chunithm.sync.completed`의 `order=30` 조각, `chunithm.song.viewed`/`chunithm.plan.requested`의 조각.

**이벤트 내용**

`chunithm.goal.completed`(알림):

```python
{
    "player_id": str,
    "goal_id": str,
    "kind": str,            # rating | overpower | folder | song | freeform
    "label": str,           # 사람이 읽을 목표 설명(유니가 그대로 인용)
    "created_at": str,
    "completed_at": str,
}
```

- [ ] **Step 1: manifest 작성**(`services: [records, songdb, notify, players, yuni]`, `listens: [chunithm.sync.completed, chunithm.song.viewed, chunithm.plan.requested]`, `emits: [chunithm.goal.completed]`, 스킬 자체 태그 없음).
- [ ] **Step 2: `set`(목표 만들기) 실패 테스트부터.** 레이팅/오버파워/폴더 목표는 함수 안에서 `chunithm-synced` 확인(태그 없으면 에러), 곡별/자유 목표는 로그인 없이도 허용. 레이팅 최종 목표는 플레이어당 하나(이미 있으면 기존 걸 덮어쓰거나 에러 — **덮어쓰지 않고 에러로 안내**, "이미 레이팅 목표가 있다. `/목표 수정`을 쓰라"고 고른다. 이 구체적 동작은 설계서에 없던 세부라 SKILL.md 본문에 적는다).
- [ ] **Step 3: `list`/`update`/`progress`/`delete` 테스트.** `list`에 `player: PlayerRef`를 주면 그 사람의 **공개** 목표만(본인이면 전체).
- [ ] **Step 4: `todo_add`/`todo_bulk`/`todo_list`/`todo_done` 테스트.** `todo_bulk`는 조건에 맞는 채보 수를 먼저 세어 `confirm="bulk"`로 버튼을 거치는지(실행기가 `confirm` 처리는 하니, 스킬은 `ToolSpec(confirm="bulk")`만 선언하면 됨 — 도구가 "몇 건" 정보를 `data`에 담아 모델이 미리 말해 줄 수 있게 하는지).
- [ ] **Step 5: `@on("chunithm.sync.completed") -> Section` 테스트(order=30).** `sync_diff`의 `chart_updates`/`unchanged_plays`로 곡별 목표 자동 완료(목표 점수 이하였다가 이상이 되면 `chunithm.goal.completed` 발행 + 상태 `achieved`), 최종 목표/폴더/오버파워 목표는 `records`의 현재값(`latest_sync_summary`/`all_pb_rows`/`latest_overpower`)으로 `goals_progress`에 한 행 추가.
- [ ] **Step 6: `@on("chunithm.song.viewed") -> Section` 테스트** — 그 곡에 내 To-Do가 있으면 상태 + 남은 어택(`records.remaining_to_target` 재사용) 조각.
- [ ] **Step 7: `@on("chunithm.plan.requested") -> Section` 테스트** — 가까운(남은 어택이 작은 순) To-Do 몇 개. 테스트가 `chunithm.plan.requested`를 `{"player_id": ...}` 페이로드로 직접 `event_bus.emit_gather`해 확인한다(결정 사항 A, yuni-bond 없음).
- [ ] **Step 8: 레이팅 목표의 "곡별 목표 달성 시 예상 레이팅" 테스트** — `project_rating_with_todos` 연결.
- [ ] **Step 9: 구현(9개 도구 + 3개 이벤트 핸들러).**
- [ ] **Step 10: `uv run pytest skills/chunithm-goals` 통과 확인.**
- [ ] **Step 11: 커밋.**

### Task 10: 마이그레이션 — `notes_notes`

**Files:**

- Create: `skills/chunithm-notes/migrations/001_init.sql`

- [ ] **Step 1: 파일 작성.**

```sql
-- skills/chunithm-notes/migrations/001_init.sql
CREATE TABLE notes_notes (
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    song_title TEXT NOT NULL,
    difficulty TEXT NOT NULL DEFAULT '',  -- 빈 문자열 = 곡 전체 메모 (결정 사항 N)
    body TEXT NOT NULL,
    public INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, game, song_title, difficulty)
);
```

- [ ] **Step 2: 커밋(Task 11과 함께).**

### Task 11: `skills/chunithm-notes` 생성

**Files:**

- Create: `skills/chunithm-notes/SKILL.md`
- Create: `skills/chunithm-notes/skill.py`
- Create: `skills/chunithm-notes/tests/test_chunithm_notes.py`

**Interfaces:**

- Consumes: `services/common/players`(없음, 실제로는 공개 메모 작성자 이름을 보여주려면 player_id만 있으면 되고 디스코드 표시 이름은 핵심이 아니라 player_id를 그대로 안내문에 쓴다 — 더 나은 표시가 필요하면 다음 리뷰에서 players 조회를 더한다. **이 축소를 SKILL.md에 적는다**), `chunithm.song.viewed`/`chunithm.plan.requested` 페이로드(Task 9와 동일 포맷).
- Produces: `chunithm.song.viewed`/`chunithm.plan.requested`의 조각.

- [ ] **Step 1: manifest 작성**(`services: [songdb, players]`, `listens: [chunithm.song.viewed, chunithm.plan.requested]`, 태그 없음).
- [ ] **Step 2: `set` 테스트 — 난이도 없이 저장(빈 문자열), 덮어쓰기, `append=True` 덧붙이기.**
- [ ] **Step 3: `get` 테스트 — 내 메모 + 공개된 남의 메모 최근 3개(+더보기 신호), 비공개 메모가 남에게 안 보이는지.**
- [ ] **Step 4: `list`/`delete` 테스트.**
- [ ] **Step 5: `@on("chunithm.song.viewed") -> Section` 테스트 — 메모 없으면 `None`을 반환(모으기에서 조각이 아예 빠짐 — `Section | None` 반환형으로 `handler_kind`가 여전히 gather로 추론되는지 Task 3에서 쓴 `core/events.py`의 `handler_kind` 동작 확인. 타입 힌트가 `Section | None`이면 `get_type_hints().__args__`에 `Section`이 포함되므로 gather로 잡힌다 — 이미 `core/events.py`가 그렇게 짜여 있다).**
- [ ] **Step 6: `@on("chunithm.plan.requested") -> Section` 테스트 — 후보 곡들의 내 메모.**
- [ ] **Step 7: 구현.**
- [ ] **Step 8: `uv run pytest skills/chunithm-notes` 통과 확인.**
- [ ] **Step 9: 커밋.**

### Part 2 통과 기준

- `uv run pytest`, `uv run ruff check .`, `uv run basedpyright` 전부 통과.
- 테스트로 확인: 동기화 한 번으로 곡별 목표가 자동 완료되고 `chunithm.goal.completed`가 나간다.
- 테스트로 확인: 레이팅 목표의 달성률·필요량·프레임 재구성 예상 레이팅이 숫자로 맞다.
- 테스트로 확인: 비공개 메모가 남의 곡 조회 조각에 안 나온다.

### 디스코드에서 직접 확인할 순서 (멘션 + 슬래시 두 길)

1. **슬래시**: `/목표 추가`로 레이팅 최종 목표(예: 현재값+0.1)를 기한 없이 만든다.
2. **멘션**: "15.0~15.3 마스 중 SSS 안 된 거 전부 투두에 넣어 줘" → 대상 개수 확인 버튼이 뜨는지, 확인하면 추가되는지.
3. **슬래시**: `/투두 목록`으로 추가된 목록 확인.
4. 실제로 그중 한 채보를 플레이해 목표 점수를 넘긴다.
5. **슬래시**: `/동기화` → 리포트에 "달성" 표시와 `chunithm.goal.completed` 반응(유니의 great 반응)이 나오는지.
6. **멘션**: "내 목표 어디까지 왔어?" → 달성률·남은 레이팅이 말로 나오는지.
7. **슬래시**: `/곡` 또는 `/메모 쓰기`로 그 채보에 메모를 남기고 공개로 바꾼다.
8. **멘션**: "이 곡 내 메모 보여 줘" / 다른 디스코드 계정(또는 테스트 계정)으로 같은 곡을 조회해 공개 메모가 보이는지.
9. **슬래시**: `/메모 삭제`로 지운 뒤 다시 조회해 안 보이는지.

---

## Part 3: chunithm-session

**통과 전제:** Part 1이 끝나야 시작한다(`chunithm.sync.reported` 페이로드가 고정된 뒤).

### Task 12: 마이그레이션 — `session_reports`

**Files:**

- Create: `skills/chunithm-session/migrations/001_init.sql`

- [ ] **Step 1: 파일 작성.**

```sql
-- skills/chunithm-session/migrations/001_init.sql
CREATE TABLE session_reports (
    session_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    play_date TEXT NOT NULL,
    rendered_text TEXT NOT NULL,
    sections_data TEXT NOT NULL,   -- JSON
    yuni_line TEXT NOT NULL,
    estimated INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT NOT NULL
);
```

(`session_reviews`는 12단계에서 만든다 — 결정 사항 O.)

- [ ] **Step 2: 커밋(Task 13과 함께).**

### Task 13: `skills/chunithm-session` 생성 — 보관과 조회만

**Files:**

- Create: `skills/chunithm-session/SKILL.md`
- Create: `skills/chunithm-session/models.py`
- Create: `skills/chunithm-session/skill.py`
- Create: `skills/chunithm-session/tests/test_chunithm_session.py`

**Interfaces:**

- Consumes: `chunithm.sync.reported` 페이로드(Task 5), `ArcadeRef`(해석만, 저장된 방문 조회는 `ctx.arcades.visits_for`), `records.pb_row`/`all_pb_rows`(그 세션 이후 변화 보여주기 — "그날 SS+였던 곡이 지금은 SSS" 비교는 **session_reports에 고정 저장된 당시 점수**(sections_data 안에 있음)와 **지금의 records_pb**를 비교해야 하므로, session_reports에 세션별 "그때 갱신된 채보 목록"을 sections_data에서 뽑아 쓴다).
- Produces: `chunithm.session.viewed`(모으기), `chunithm.session.closed`는 **선언하지 않음**(결정 사항 O).

**이벤트 내용**

`chunithm.session.viewed`(모으기):

```python
{"player_id": str, "session_id": str, "play_date": str}
```

- [ ] **Step 1: manifest 작성**(`services: [records, arcades, yuni, notify, players]`, `tags: [chunithm-synced]`, `listens: [chunithm.sync.reported]`, `emits: [chunithm.session.viewed]`).
- [ ] **Step 2: `@on("chunithm.sync.reported")` 테스트부터** — 새 플레이가 있는 리포트만 보관하고(`has_new_plays=False`면 아무것도 안 함, 이게 반환형 없는 notify 핸들러인지 확인 — `core/events.py`의 `handler_kind`가 반환 타입 `None`이면 notify로 잡는다), `sessions` 목록마다 `session_reports` 행 하나씩 생긴다.
- [ ] **Step 3: `list` 테스트 — 날짜/기간/오락실 조합, 없을 때 가장 가까운 앞뒤 세션 제안.**
- [ ] **Step 4: `get` 테스트 — 저장된 요약(1) + `chunithm.session.viewed` 모으기로 받은 조각(2, 받는 스킬이 아직 없으니 빈 리스트) + 그 뒤의 변화(3, 그때 점수 vs 지금 `records.pb_row`) + 유니의 덧붙임(5, 멘션일 때만 `ctx.yuni.line` 한 번) 순서대로.** "후기"(4)는 생략.
- [ ] **Step 5: `get`의 비공개 처리 테스트(결정 사항 I) — 본인 조회는 그 자리 그대로, 남 조회는 `records_public` 확인.**
- [ ] **Step 6: `delete` 테스트 — `confirm="always"`, 본인 세션만.**
- [ ] **Step 7: 날짜 추정(`estimated=True`) 표시가 `get`/`list` 결과에 실제로 드러나는지 테스트.**
- [ ] **Step 8: 구현(4개 도구 + 1개 이벤트 핸들러).**
- [ ] **Step 9: `uv run pytest skills/chunithm-session` 통과 확인.**
- [ ] **Step 10: 커밋.**

### Part 3 통과 기준

- `uv run pytest`, `uv run ruff check .`, `uv run basedpyright` 전부 통과.
- 테스트로 확인: 변화 없는 동기화는 보관되지 않는다.
- 테스트로 확인: 저장된 요약은 이후 상수 변경/재동기화로도 안 바뀌고, "그 뒤의 변화"만 지금 값을 다시 계산해 보여준다.
- 테스트로 확인: 추정 날짜 세션이 화면에 표시로 드러난다.

### 디스코드에서 직접 확인할 순서 (멘션 + 슬래시 두 길)

1. (Part 1/2에서 이미 몇 번 동기화를 돌려 세션이 2개 이상 쌓인 상태로 시작한다.)
2. **슬래시**: `/세션 목록`으로 이번 달 세션 날짜들이 나오는지.
3. **멘션**: "가장 최근 겜플 세션 불러와 줘"(오락실 이름이 등록돼 있다면) / 없다면 "가장 최근 세션 어땠어?".
4. **슬래시**: `/세션 보기`로 특정 날짜를 지정해, 당시 리포트 그대로 + 지금 기준 변화가 함께 나오는지.
5. 그 세션 이후 같은 곡을 다시 쳐서 랭크를 올린 뒤 다시 `/세션 보기` → "그 뒤의 변화"에 반영되는지.
6. **슬래시**: `/세션 삭제`(확인 버튼 포함)로 지운 뒤 `/세션 목록`에서 빠졌는지.

---

## 계획 마지막: examples 점검 + 평가 재실행

### Task 14: 다섯 스킬의 `examples`로 도구 선택 정확도 재평가

**Files:**

- Modify: `skills/chunithm-sync/SKILL.md`, `skills/chunithm-report/SKILL.md`, `skills/chunithm-goals/SKILL.md`, `skills/chunithm-notes/SKILL.md`, `skills/chunithm-session/SKILL.md` (이미 각 스킬 문서의 `examples`를 그대로 썼다면 추가 변경 없음 — 비어 있으면 여기서 채운다)
- Run: `scripts/eval_agent.py`

- [ ] **Step 1: 다섯 스킬 manifest의 `examples`가 각 설계서의 `examples`와 같은지 확인(Task 5/6/9/11/13에서 이미 채웠을 것 — 빠진 게 있으면 채운다).**
- [ ] **Step 2: `GEMINI_API_KEY`가 있는 환경에서 `uv run python3 scripts/eval_agent.py --compare-modes --model gemini-3.8-flash` 실행.**
- [ ] **Step 3: `uv run python3 scripts/eval_agent.py --compare-models gemini-3.8-flash,gemini-3.5-flash-lite` 실행.**
- [ ] **Step 4: 두 결과의 "도구 선택 오류"·"숫자 규칙 위반" 건수를 기록하고, 6단계 때의 기준(이미 결정된 `instructions`/`full` 전환 기준)과 비교해 바뀐 게 있으면 `docs/design/00-core.md`의 "스킬 불러오기" 절 각주에 남긴다.**
- [ ] **Step 5: 커밋(결과를 어딘가 기록했다면 그 파일만 — 코드 변경이 없으면 이 Task는 평가 기록용 커밋 하나로 끝난다).**

---

계획이 끝났다. 세 부(Part 1\~3)는 반드시 이 순서로 진행한다. 구현은 세 번의 세션(세 부 각각)으로 나눠 하고, 부마다 "통과 기준"을 실제로 확인한 뒤 다음 부로 넘어간다.
