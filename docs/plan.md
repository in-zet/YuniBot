# 계획

기능 세부는 설계서(`docs/design/00-core.md`, `docs/design/skills/<스킬>.md`)가 기준이다. 이 문서는 범위·단계·결정·리스크만 다룬다.

## 1. 범위

**포함** — 스킬 19개(설계서 "스킬 문서 목록"). 지인 서버 10명 미만, 국제판만(일본판은 업데이트 알림만).

| 스킬                                                             | 단계 | 상태   |
| ---------------------------------------------------------------- | ---- | ------ |
| account-link, admin                                              | 3    | 구현   |
| chunithm-account, chunithm-song, chunithm-border, chunithm-terms | 5    | 구현   |
| chunithm-sync, chunithm-report                                   | 7    | 구현   |
| chunithm-goals, chunithm-notes, chunithm-session                 | 7    | 구현   |
| chunithm-arcade                                                  | 8    | 구현   |
| chunithm-updates                                                 | 9    | 구현   |
| web-qa                                                           | 10   | 구현   |
| memory                                                           | 12   | 구현   |
| yuni-bond                                                        | 12   | 미착수 |
| chunithm-settings                                                | 13   | 구현   |
| chunithm-folders                                                  | 14   | 구현   |
| yuni-lore                                                         | 15   | 구현   |

**제외** (검토하고 버린 안 — 재논의 전까지 다시 제안하지 않는다)

- 스킬마다 MCP 서버: 이벤트·예약·버튼·권한이 MCP 범위 밖이다. 도구 정의만 MCP 형식에 맞춰 둔다.
- 라우터 모델(싼 모델이 먼저 스킬 분류): 호출이 늘고 분류 오류를 본 모델이 못 고친다. `open_skill`로 본 모델이 고른다.
- 슬래시 중심·LLM 최소: 유니 중심이라는 목표와 반대다(대부분의 도구는 슬래시로도 열려 있다).
- 스킬 없이 도구 전부 넘기기, 스킬끼리 직접 호출.
- 다른 게임용 공통 추상 계층(두 번째 게임이 실제로 정해질 때 만든다).

## 2. 결정 기록

설계서에 옮겨 적은 것은 여기 반복하지 않는다. 스킬별 결정은 각 스킬 문서 끝의 "구현 결정" 절에 있다.

| 항목                         | 결정                                                                                                                               | 근거·메모                                                                                                               |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 작업 방식                    | 2026-10-18부터 superpowers(계획서·서브에이전트 구동)를 쓰지 않고 `CLAUDE.md` 워크플로를 따른다                                  | 토큰 소모. 옛 계획서 7개(약 850KB)는 삭제, 결정만 설계서와 이 문서로 옮겼다. 원본은 git 이력(`progress/2026-10-18.md`) |
| 커밋                         | 사용자가 요청할 때만 한다(2026-10-18 변경, 이전엔 작업 끝나면 자동 커밋·병합)                                                     | 사용자 검토 후 반영                                                                                                      |
| LLM 제공자                   | 기본 Gemini(에이전트 3.8 Flash, 요약 3.5 Flash-Lite), `LLM_PROVIDER=claude`로 Claude 선택 가능(2026-10-17)                       | 설계서 "비용과 운영"                                                                                                     |
| 서비스 확장                  | 스킬에 필요한 메서드를 서비스에 더하는 것은 "다른 스킬을 고치는 것"이 아니다. 생성자에 새 인자를 넣을 때는 키워드·기본값 `None` | 기존 호출부·시험을 깨지 않기 위해                                                                                       |
| `/동기화` 쿨다운           | 서버별 설정 "동기화 쿨다운"(기본 120초) — `chunithm-sync`의 services에 `settings` 추가                                        | 설계서의 설정 표를 그대로 구현                                                                                           |
| `chunithm-report` services | `chunithm_net` 추가(`recent`가 그 자리에서 판정 수를 가져옴, 앞 20개만 상세 조회)                                              | 저장된 판정 수가 없다                                                                                                    |
| "분석"의 판정 수 비중        | 뺐다                                                                                                                               | PB에는 판정 수가 저장되지 않는다                                                                                         |
| 동기화 리포트 비공개         | 슬래시·멘션 모두 DM으로 전문, 부른 자리엔 짧은 확인만                                                                             | 도구가 호출 경로·채널을 모른다                                                                                          |

## 3. 구조

폴더 구조는 `docs/INDEX.md`, 코어 구조는 설계서 "전체 구조", 개발 규칙은 `docs/conventions.md`.

## 4. 작업 단계

설계서의 12단계를 한 번에 하나씩 한다. 체크 항목 하나가 작업 단위다. "실기"는 디스코드에서 사람이 직접 확인할 것이다.

- [X] 1. 저장소와 가져온 코드(`third_party/`, `sync_upstream.py`, Docker) — 실기 미확인: 미니 PC에서 `docker compose up -d --build`
- [X] 2. 코어 뼈대(레지스트리·데코레이터·슬래시·도구 실행기·이벤트 버스·스레드·마이그레이션·계약 시험) — 실기 미확인: 연습 스킬(`tests/fixtures/skills_ok/practice`)로 확인 버튼·상태 태그 잠금·부여 태그 숨김·불량 스킬 격리
- [X] 3. 공통 서비스 + `account-link`·`admin` — 실기 미확인: 두 계정 연결, `/관리` 권한, 서버별 스킬 끄기, 재시작 후 설정 유지
- [X] 4. 츄니즘 서비스(`chunithm_net`·`songdb`·`records`·`terms`)
- [X] 5. 첫 스킬 4개(account·song·border·terms) — 실기 미확인: 실제 국제판 계정 `/로그인`→`/계정`→`/로그아웃`, 곡·별칭·보더·용어 명령
- [X] 6. 에이전트(LLM 어댑터·`open_skill`·표시물·숫자 대조·비용 상한·`list_capabilities`) — 실기 미확인: 멘션 응답, 답장 사슬, 쿨다운·월상한 문구, `/관리 사용량`
  - [ ] `scripts/eval_agent.py`로 `instructions`/`full` 모드 비교와 두 모델 비교 — 결과 기록이 없다(LLM 키 필요, 비용 발생)
- [X] 7. 기록 스킬
  - [X] Part 1: `chunithm-sync`·`chunithm-report`(서비스 확장 포함). 실기 미확인: 첫 동기화 기준점 → 플레이 후 리포트 → 비공개 DM → `/프로필`·`/베스트`·`/최근`·`/분석`·`/후보곡` → 변화 없음
  - [X] Part 2: `chunithm-goals`·`chunithm-notes`(마이그레이션 `goals_goals`·`goals_progress`·`notes_notes`, 순수 함수 `goals_calc.py` 먼저) — 구현 완료. `chunithm.plan.requested` 조각은 받는 쪽만 있고 내는 쪽(`yuni-bond`)은 12단계
  - [X] Part 3: `chunithm-session`(보관·조회만) — 구현 완료(`list_sessions`·`get_session`·`delete_session`, `on_sync_reported`). 실기 미확인: 동기화 후 세션 보관 → `/세션 목록`·`/세션 보기`(날짜·오락실 지정 포함)·`/세션 삭제`
  - [X] 다섯 스킬의 `examples`로 도구 선택 재평가 — `gemini-3.8-flash`/`instructions` 모드, 등록된 스킬 전체(27건, `account-link`는 LLM 도구가 없어 제외) 도구 선택 오류 0건·숫자 규칙 위반 0건. `full` 모드·모델 비교는 안 함(§5 참고)
- [X] 8. 오락실 `chunithm-arcade` — 구현 완료(`get_settings`·`history`·`record_visit`·`set_privacy`, `on_sync_completed`·`on_sync_reported`·`on_session_viewed`). 실기 미확인: 동기화 때 오락실 말하기 → 설정 저장 → `/오락실 설정`·`/오락실 기록`·`/오락실 방문`, 세션 조회에 그날 설정이 뜨는지, 공개 설정 전환
- [X] 9. 알림 `chunithm-updates`, 목표 기한 알림(운영 중 예약 확인 루프를 이때 `core/bot.py`에 더한다) — 세 파트로 나눔(2026-10-18)
  - [X] Part 1: 코어. `ctx.scheduler`(`core/registry.py`의 `CTX_FIELD_SERVICE_NAMES`에 추가, db/player_data 등과 같은 결), `Scheduler.cancel_once`·`poll_due_recurring`·`poll_due_once`, `core/bot.py`의 `YuniClient._poll_schedules`(60초 폴링 루프, `discord.ext.tasks.loop`). 상세는 `00-core.md` "예약 작업"
  - [X] Part 2: `chunithm-updates` 스킬(공지·곡목록 확인, `catch_up`, `next`/`new_songs`/`configure`, 동기화 리포트 조각 order 40) 구현 완료. 저장본 `docs/reference/chunithm-updates/`에 저장. 구현 중 바뀐 점은 `chunithm-updates.md` "구현 결정" 참고. 실기 미확인: `/업데이트 설정`으로 채널 지정 → 실제 공지/곡목록 확인 주기가 돌아 채널에 알리는지, `/업데이트 일정`·`/신곡`, 버전업 DM, 동기화 리포트에 신곡 조각이 뜨는지
  - [X] Part 3: `chunithm-goals` 기한 알림(목표 생성/수정 때 `notify_offsets`만큼 `schedule_once`, 기한 전 달성 시 취소, 기한 다음날 3일 대기 후 최종 판정) 구현 완료. 상세는 `chunithm-goals.md` "구현 결정". 실기 미확인: 목표 기한을 가깝게 잡아 리마인더가 실제로 채널/DM에 오는지, 기한 전 달성 시 남은 알림이 끊기는지, 기한이 지난 목표의 최종 판정 메시지와 반응
- [X] 10. 검색 답변 `websearch` 서비스와 `web-qa` — 세 파트로 나눔(2026-10-18)
  - [X] Part 1: 코어. `core/decorators.py`의 `ToolSpec`에 `max_calls_per_turn`(도구별 응답당 호출 상한, 전역 `max_tool_calls_per_turn`보다 좁게) 추가, `core/agent.py`가 넘긴 호출만 에러로 돌리고 나머지 응답은 계속 돌게 적용. 아직 어느 스킬도 이 값을 쓰지 않는다(Part 3의 `web-qa.search`가 처음 쓴다)
  - [X] Part 2: `websearch` 공통 서비스. 어댑터 프로토콜(`core/search.py`)과 Gemini/Claude 구현(`core/search_gemini.py`·`core/search_claude.py`) + `services/common/websearch.py`(쿨다운·출처없음 검사). `WEBSEARCH_PROVIDER`(기본 gemini, `LLM_PROVIDER`와 독립)·`SEARCH_MODEL`·`WEBSEARCH_COOLDOWN_SECONDS`(기본 15초) 설정 추가. 상세는 `progress/2026-10-18.md`
  - [X] Part 3: `web-qa` 스킬. 구현 완료 — `search(question)` 도구 하나(`weight: heavy`, `services: [websearch, usage]`, `max_calls_per_turn=5`, 슬래시 없음, 테이블 없음, 출처는 `display_mode="required"`로 강제), 성공 시 도구 안에서 바로 `usage.record(kind="websearch")`. 상세는 `web-qa.md` "구현 결정". 실기 미확인(위 문서 "미확인 사항")
- [X] 11. 유니 인격: `persona.md`, `yuni` 서비스의 한마디(LLM), 고정 문구(`core/fallback_messages.py`) — 구현 완료(2026-10-19, 밝은 반말). 실기 미확인: 동기화 리포트·목표 알림·업데이트 알림의 한마디가 말투대로 나오는지, 한마디 사용량이 `/관리 사용량`에 잡히는지, 멘션 응답 말투
- [X] 12. 메모리와 관계 — 세 부분으로 나눔(2026-10-19), 모두 구현 완료
  - [X] Part 1: 메모리 전체. 호칭 칸(`player_memory.nickname`)과 호칭 우선순위(`core/address.py`), 자동 요약(5분 대기 목록 `player_memory_pending`, `YuniService.merge_memory`), `remember`의 `nickname`, 요약 반영 반응(📝), 표시물 `private`(멘션에서 DM), `memory` 스킬(`view`·`forget`·`set_auto`). 상세는 `00-core.md` "플레이어 메모리"와 `memory.md` "구현 결정". 실기 미확인: "호칭은 ○○로 해 줘" 후 호칭 사용, 5분 뒤 📝 반응과 메모리 반영, `/메모리 보기`(본인에게만)·멘션으로 "뭐 기억해?"(DM), `/메모리 지우기` 확인 버튼, 지운 항목이 다음 요약에서 되살아나지 않는지, `/메모리 자동저장` 끄면 요약이 안 도는지, 동기화 리포트 한마디에 호칭이 들어가는지
  - [X] Part 2: 대화 상태와 `yuni-bond`. 펼친 스킬의 답장 사슬 유지(`conversation_skills` 테이블, 동시 5개 상한), 대화 스레드를 `on_message`·유휴 점검(60초 폴링)·종료에 연결하고 닫힐 때 메모리 요약 대기 목록에 전문 등록(`YuniService.summarize_conversation`은 없앰), 코어가 `@prompt_context`를 프롬프트에 넣음, `yuni-bond`(테이블 2개, 도구 5개, 이벤트 4개 수신, `chunithm.plan.requested` 발신, 슬래시 `/기록들`·`/오늘계획`·`/약속`, 설정 2개). 상세는 `00-core.md` 구현 결정(Part 2)과 `yuni-bond.md` "구현 결정". 올 저스티스 첫 달성은 램프 변화 자료가 없어 뺌. 실기 미확인: 유니 답장에 답장했을 때 펼친 스킬이 이어지는지(로그의 도구 호출 수), 동기화 뒤 리포트 한마디에 마일스톤·첫 SSS·오랜만 귀띔이 들어가는지, `/기록들`·`/오늘계획`(To-Do·메모·채보 링크)·`/약속`, "다음엔 크로노 연습할게"로 약속 저장 후 다음 멘션에서 유니가 한 번 꺼내는지, 약속 속 곡을 동기화로 갱신하면 약속이 닫히는지, `/설정`에 "동기화 때 덧붙이는 말"이 보이고 끄면 귀띔이 빠지는지
  - [X] Part 3: `chunithm-session`의 후기. `review_open`(`/후기`)·리포트 뒤 후기 질문·`session_reviews` 저장·`chunithm.session.closed`, 코어에 DM 대화 열기(`NotifyService.open_dm_thread`)·대화 안 지침(`ThreadSession.guidance`)·`end_conversation` 도구(말로 끝내기)·끝내기 버튼(`core/thread_view.py`)·유니 발언 전문 기록. 스레드가 아니라 DM에서 연다(2026-10-19 사용자 결정, `chunithm-session.md` "구현 결정 (2026-10-19, 12단계 Part 3)"). 실기 미확인: 새 플레이 동기화 뒤 리포트 다음에 DM으로 후기 질문이 오는지, DM에 답하면 멘션 없이 짧게 답하는지(한두 문장), "끝났어"에 마무리 말과 함께 닫히는지·애매한 말에는 되묻는지, 끝내기 버튼(본인만, 누르면 버튼 사라짐), 5분 무응답 뒤 "다 끝났나요?"·다시 5분 뒤 닫힘, `/세션 보기`에 후기가 붙는지(길면 잘림), `/후기`로 지난 날짜 후기 열기, `/설정`의 "후기 물어보기"·"후기 무응답 확인 시간"·"후기 종결 시간", 대화 중 봇을 끄면 후기가 중단으로 저장되는지, 후기 대화에서 한 약속이 `yuni-bond`에 저장되는지
13~16단계는 2026-10-20에 계획 밖에서 더한 것이다. 상세와 실기 미확인 목록은 `docs/plan/2026-10-20_수정.md`의 같은 번호 절에 있다.

- [X] 13. `chunithm-settings`(인게임 설정 조회·변경, 즐겨찾기) — `2026-10-20_수정.md` §13
- [X] 14. `chunithm-folders`(채보 폴더) — `2026-10-20_수정.md` §14
- [X] 15. `yuni-lore`(설정집 읽기) — `2026-10-20_수정.md` §15
- [ ] 16. 작업 표(설계 `00-core.md` "작업 표" 초안, 결정 대기 모두 정리·구현 전) — `2026-10-20_수정.md` §16
  - [X] Part 0: 동기화 때 플레이어 레벨·캐릭터 그림 주소 저장 — 구현 완료(2026-10-20, `records_profile`·`RecordsService.player_profile`). 실기 미확인: 실제 동기화 뒤 `records_profile`에 레벨·그림 주소가 들어가는지
  - [X] Part 1: 코어(표 구조·보관소·`TableRef`·`table_query`/`table_peek`·글자 출력·`Display.files`) — 구현 완료(2026-10-20). 세부 파트로 나눠 하다가 사용자가 "쭉 해라"고 해 1-5부터 한 번에 이었다. 실기 미확인: 표를 낸 도구가 아직 없어(Part 2부터) 실제 대화 확인은 Part 2 뒤에 한다. 시험은 세부 파트마다 그 파트 몫을 함께 쓴다(수정 문서 1-10을 나눠 담는다)
    - [X] 1-1 표 구조(`core/tables.py`: `WorkTable`·`Column`·`Cell`·`Row`·`Group`·`TableBuilder`·`row_at`) — 구현 완료(2026-10-20)
    - [X] 1-2 보관소(`TableStore`·`ToolContext.tables`·`channel_id`·만료·옛 값 표시) — 구현 완료(2026-10-20)
    - [X] 1-3 도구 결과와 인자(`ToolResult.table`·`TableRef`·`TableNeeds`·실행기의 표 해석) — 구현 완료(2026-10-20)
    - [X] 1-4 에이전트 처리(표 요약·`last_table_id` 등) — 구현 완료(2026-10-20)
    - [X] 1-5 코어 내장 도구(`select_rows`·`table_query`·`table_peek`) — 구현 완료(2026-10-20)
    - [X] 1-6 글자 출력(`render_text`·`row_text`·응답 조립) — 구현 완료(2026-10-20)
    - [X] 1-7 첨부 파일과 그림 표 표시(`Display.files`·`drew_table`·슬래시 경로) — 구현 완료(2026-10-20). 실기 미확인: 11행 이상 표가 .txt 첨부로 나가는지(멘션·슬래시)
    - [X] 1-8 수명 연결(`core/mention.py`: 묶기·`retain_only`·프롬프트 조각·번호 기준 줄) — 구현 완료(2026-10-20)
    - [X] 1-9 프롬프트(`prompts/rules.md`) — 구현 완료(2026-10-20)
  - [ ] Part 2: 첫 범위 스킬(곡 검색 4개·`best`·`add_scores`·폴더 보기/저장) — 세부 파트
    - [X] 2-1 공통 칸 `services/chunithm/table_columns.py` — 구현 완료(2026-10-20)
    - [X] 2-2 `chunithm-song` 곡 검색 4개 표로(`find_songs`의 `sort_by`·`descending` 제거, `find_by_artist`에 `difficulty`) — 구현 완료(2026-10-20)
    - [ ] 2-3 `chunithm-report` `best` 표 + `add_scores`
    - [ ] 2-4 `chunithm-folders` `show_folder` 표 + `save_table`
    - [ ] 2-5 `chunithm-sync` `mark_stale` + `core/bot.py` `partial_row_hints` 연결
    - [ ] 2-6 `rules.md` 정리·문서
  - [ ] Part 3: 그림 표 `chunithm-image`
  - [ ] Part 4: 나머지 목록 도구를 표로(15개)
  - [ ] Part 5: 표를 받아 칸을 더하거나 곡 행을 채보로 펼치는 도구(6개)
  - [ ] Part 6: id 입구 나누기(슬래시 전용 + 표 받는 모델 도구)와 표로 데이터 바꾸기 — Part 1~5 뒤

고정 원칙: 5단계까지는 LLM 없이 슬래시로 동작한다. `yuni` 서비스는 11단계 전까지 정해진 문구를 돌려주고, 7~10단계 스킬은 이 인터페이스만 쓴다.

## 5. 알려진 리스크

| 항목                                                | 상태                                                                                                                                                                                                                                                                                                      | 대응                                                                                         |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Gemini 일시 오류(503 과부하 등)·무응답 | 해결(2026-10-18 백오프 재시도, 2026-10-19 요청 타임아웃 추가): `core/gemini_retry.py`가 429·5xx를 최대 3번, 타임아웃 504는 1번 재시도하고, 끝내 실패하면 `core/mention.py`가 고정 문구로 답한다. 2026-10-19 gemini-3.8-flash가 몇십 분간 무응답이었다 | 대체 모델 자동 전환은 없다. 필요해지면 그때 정한다 |
| 실기 확인 기록 없음                                 | 1~7단계 모두 디스코드 실기 결과가 저장소에 없다                                                                                                                                                                                                                                                           | §4의 "실기 미확인"을 사람이 확인하고 `progress/`에 남긴다                                 |
| `cost="network"` 등급별 호출 상한 미구현          | network 도구(`/동기화`, `/최근`)가 이미 있다                                                                                                                                                                                                                                                          | 전역 "응답당 5회"만 집행 중. 8·10단계 전에 정한다                                           |
| `httpx-aiohttp` 패치가 업스트림의 재시도를 잃음   | PyPI판 `AiohttpTransport`는 `retries`를 받지만 재시도하지 않는다                                                                                                                                                                                                                                      | `third_party/patches/README.md`. CHUNITHM-NET 재시도를 서비스에서 짤지 정해야 한다         |
| 국제판 버전업                                       | 시기 미정                                                                                                                                                                                                                                                                                                 | 직후 파서와 신곡 기준일을 점검하고 원본 어댑터 수정을 가져온다                               |
| 레이팅·오버파워가 CHUNITHM-NET과 맞는지            | 확인 못 함                                                                                                                                                                                                                                                                                                | `records`가 동기화마다 대조하고 어긋나면 리포트에 표시                                     |
| `open_skill` 왕복의 지연·비용                    | `full` 모드 측정 전(비교용 데이터 없음)                                                                                                                                                                                                                                                                 | `--compare-modes`로 다음에 측정                                                            |
| Gemini가 안정적으로 고르는 도구 수                  | 2026-10-18 확인: `instructions` 모드에서 태그를 올바로 쥐여 주면 등록 스킬 전체(27건, account-link 제외) 도구 선택 오류 0건. 이전 측정(13/29 오류)은 평가 플레이어에게 태그가 전혀 없어 스킬 카드 자체가 안 보인 하네스 버그였다(`core/prompt.py`의 `visible_skill_cards`가 플레이어 태그로 거른다) | 해결(하네스 버그), `scripts/eval_agent.py`가 이제 등록된 스킬의 태그를 모두 모아 쥐여 준다 |
| 모으기 이벤트에서 느린 스킬                         | 예상 위험                                                                                                                                                                                                                                                                                                 | 스킬별 시간 제한(기본 5초), 넘기면 그 조각만 뺀다                                            |
| PlayerRef 자유 텍스트 이름 매칭, `/설정` 자동완성 | 없음                                                                                                                                                                                                                                                                                                      | 필요해지면 그때 만든다                                                                       |
| `terms.yaml`의 개념 용어 4개                      | 2026-10-11 임시 등록(저스티스 크리티컬·안미츠/비비기·상수·오버파워), 출처 없음                                                                                                                                                                                                                         | 사용자 검토 필요                                                                             |
| LLM 가격과 모델                                     | 수시로 바뀐다. 3.8 Flash 할인가는 2026-12-31까지                                                                                                                                                                                                                                                          | 2027년 1월에 가격표와 `core/llm_pricing.py`를 다시 확인                                    |
| 공식 캐릭터를 인격으로 쓰는 권리 문제               | 판단 불가                                                                                                                                                                                                                                                                                                 | 비공식 팬 제작 표기, 공식 음성·일러스트 재배포 금지, 지인 서버 밖 공개 안 함                |
| 스킬은 샌드박스가 아니다                            | 설계상 한계                                                                                                                                                                                                                                                                                               | 저장소에 들어오는 스킬을 검토한다                                                            |
