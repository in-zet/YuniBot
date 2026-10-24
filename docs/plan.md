# 계획

기능 세부는 설계서(`docs/design/00-core.md`, `docs/design/skills/<스킬>.md`)가 기준이다. 이 문서는 범위·단계·결정·리스크만 다룬다.

## 1. 범위

**포함** — 스킬 19개(설계서 "스킬 문서 목록"). 지인 서버 10명 미만, 국제판만(일본판은 업데이트 알림만). 게임은 츄니즘과 maimai DX(17단계부터, 설계서 "여러 게임").

| 스킬                                                             | 단계 | 상태   |
| ---------------------------------------------------------------- | ---- | ------ |
| account-link, admin                                              | 3    | 구현   |
| sega-account, song, chunithm-border, terms                       | 5    | 구현   |
| sync, report                                                     | 7    | 구현   |
| goals, notes, session                                            | 7    | 구현   |
| arcade                                                           | 8    | 구현   |
| updates                                                          | 9    | 구현   |
| web-qa                                                           | 10   | 구현   |
| memory                                                           | 12   | 구현   |
| yuni-bond                                                        | 12   | 미착수 |
| settings                                                         | 13   | 구현   |
| folders                                                          | 14   | 구현   |
| yuni-lore                                                        | 15   | 구현   |
| (maimai DX — 기존 스킬에 게임 매개변수)                          | 17   | 미착수 |

**제외** (검토하고 버린 안 — 재논의 전까지 다시 제안하지 않는다)

- 스킬마다 MCP 서버: 이벤트·예약·버튼·권한이 MCP 범위 밖이다. 도구 정의만 MCP 형식에 맞춰 둔다.
- 라우터 모델(싼 모델이 먼저 스킬 분류): 호출이 늘고 분류 오류를 본 모델이 못 고친다. `open_skill`로 본 모델이 고른다.
- 슬래시 중심·LLM 최소: 유니 중심이라는 목표와 반대다(대부분의 도구는 슬래시로도 열려 있다).
- 스킬 없이 도구 전부 넘기기, 스킬끼리 직접 호출.

## 2. 결정 기록

설계서에 옮겨 적은 것은 여기 반복하지 않는다. 스킬별 결정은 각 스킬 문서 끝의 "구현 결정" 절에 있다.

| 항목                         | 결정                                                                                                                               | 근거·메모                                                                                                               |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 작업 방식                    | 2026-10-18부터 superpowers(계획서·서브에이전트 구동)를 쓰지 않고 `CLAUDE.md` 워크플로를 따른다                                  | 토큰 소모. 옛 계획서 7개(약 850KB)는 삭제, 결정만 설계서와 이 문서로 옮겼다. 원본은 git 이력(`progress/2026-10-18.md`) |
| 커밋                         | 사용자가 요청할 때만 한다(2026-10-18 변경, 이전엔 작업 끝나면 자동 커밋·병합)                                                     | 사용자 검토 후 반영                                                                                                      |
| LLM 제공자                   | 기본 Gemini(에이전트 3.8 Flash, 요약 3.5 Flash-Lite), `LLM_PROVIDER=claude`로 Claude 선택 가능(2026-10-17)                       | 설계서 "비용과 운영"                                                                                                     |
| 서비스 확장                  | 스킬에 필요한 메서드를 서비스에 더하는 것은 "다른 스킬을 고치는 것"이 아니다. 생성자에 새 인자를 넣을 때는 키워드·기본값 `None` | 기존 호출부·시험을 깨지 않기 위해                                                                                       |
| `/동기화` 쿨다운           | 서버별 설정 "동기화 쿨다운"(기본 120초) — `sync`의 services에 `settings` 추가                                        | 설계서의 설정 표를 그대로 구현                                                                                           |
| `report` services | `chunithm_net` 추가(`recent`가 그 자리에서 판정 수를 가져옴, 앞 20개만 상세 조회)                                              | 저장된 판정 수가 없다                                                                                                    |
| "분석"의 판정 수 비중        | 뺐다                                                                                                                               | PB에는 판정 수가 저장되지 않는다                                                                                         |
| 두 번째 게임                 | 국제판 maimai DX(2026-10-23). 게임별 스킬을 따로 만들지 않고 게임 매개변수로 확장, 두 게임 스킬은 게임 중립 이름 | `2026-10-23_수정.md` §17, 설계서 "여러 게임" |
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
  - [X] Part 1: `sync`·`report`(서비스 확장 포함). 실기 미확인: 첫 동기화 기준점 → 플레이 후 리포트 → 비공개 DM → `/프로필`·`/베스트`·`/최근`·`/분석`·`/후보곡` → 변화 없음
  - [X] Part 2: `goals`·`notes`(마이그레이션 `goals_goals`·`goals_progress`·`notes_notes`, 순수 함수 `goals_calc.py` 먼저) — 구현 완료. `chunithm.plan.requested` 조각은 받는 쪽만 있고 내는 쪽(`yuni-bond`)은 12단계
  - [X] Part 3: `session`(보관·조회만) — 구현 완료(`list_sessions`·`get_session`·`delete_session`, `on_sync_reported`). 실기 미확인: 동기화 후 세션 보관 → `/세션 목록`·`/세션 보기`(날짜·오락실 지정 포함)·`/세션 삭제`
  - [X] 다섯 스킬의 `examples`로 도구 선택 재평가 — `gemini-3.8-flash`/`instructions` 모드, 등록된 스킬 전체(27건, `account-link`는 LLM 도구가 없어 제외) 도구 선택 오류 0건·숫자 규칙 위반 0건. `full` 모드·모델 비교는 안 함(§5 참고)
- [X] 8. 오락실 `arcade` — 구현 완료(`get_settings`·`history`·`record_visit`·`set_privacy`, `on_sync_completed`·`on_sync_reported`·`on_session_viewed`). 실기 미확인: 동기화 때 오락실 말하기 → 설정 저장 → `/오락실 설정`·`/오락실 기록`·`/오락실 방문`, 세션 조회에 그날 설정이 뜨는지, 공개 설정 전환
- [X] 9. 알림 `updates`, 목표 기한 알림(운영 중 예약 확인 루프를 이때 `core/bot.py`에 더한다) — 세 파트로 나눔(2026-10-18)
  - [X] Part 1: 코어. `ctx.scheduler`(`core/registry.py`의 `CTX_FIELD_SERVICE_NAMES`에 추가, db/player_data 등과 같은 결), `Scheduler.cancel_once`·`poll_due_recurring`·`poll_due_once`, `core/bot.py`의 `YuniClient._poll_schedules`(60초 폴링 루프, `discord.ext.tasks.loop`). 상세는 `00-core.md` "예약 작업"
  - [X] Part 2: `updates` 스킬(공지·곡목록 확인, `catch_up`, `next`/`new_songs`/`configure`, 동기화 리포트 조각 order 40) 구현 완료. 저장본 `docs/reference/chunithm-updates/`에 저장. 구현 중 바뀐 점은 `updates.md` "구현 결정" 참고. 실기 미확인: `/업데이트 설정`으로 채널 지정 → 실제 공지/곡목록 확인 주기가 돌아 채널에 알리는지, `/업데이트 일정`·`/신곡`, 버전업 DM, 동기화 리포트에 신곡 조각이 뜨는지
  - [X] Part 3: `goals` 기한 알림(목표 생성/수정 때 `notify_offsets`만큼 `schedule_once`, 기한 전 달성 시 취소, 기한 다음날 3일 대기 후 최종 판정) 구현 완료. 상세는 `goals.md` "구현 결정". 실기 미확인: 목표 기한을 가깝게 잡아 리마인더가 실제로 채널/DM에 오는지, 기한 전 달성 시 남은 알림이 끊기는지, 기한이 지난 목표의 최종 판정 메시지와 반응
- [X] 10. 검색 답변 `websearch` 서비스와 `web-qa` — 세 파트로 나눔(2026-10-18)
  - [X] Part 1: 코어. `core/decorators.py`의 `ToolSpec`에 `max_calls_per_turn`(도구별 응답당 호출 상한, 전역 `max_tool_calls_per_turn`보다 좁게) 추가, `core/agent.py`가 넘긴 호출만 에러로 돌리고 나머지 응답은 계속 돌게 적용. 아직 어느 스킬도 이 값을 쓰지 않는다(Part 3의 `web-qa.search`가 처음 쓴다)
  - [X] Part 2: `websearch` 공통 서비스. 어댑터 프로토콜(`core/search.py`)과 Gemini/Claude 구현(`core/search_gemini.py`·`core/search_claude.py`) + `services/common/websearch.py`(쿨다운·출처없음 검사). `WEBSEARCH_PROVIDER`(기본 gemini, `LLM_PROVIDER`와 독립)·`SEARCH_MODEL`·`WEBSEARCH_COOLDOWN_SECONDS`(기본 15초) 설정 추가. 상세는 `progress/2026-10-18.md`
  - [X] Part 3: `web-qa` 스킬. 구현 완료 — `search(question)` 도구 하나(`weight: heavy`, `services: [websearch, usage]`, `max_calls_per_turn=5`, 슬래시 없음, 테이블 없음, 출처는 `display_mode="required"`로 강제), 성공 시 도구 안에서 바로 `usage.record(kind="websearch")`. 상세는 `web-qa.md` "구현 결정". 실기 미확인(위 문서 "미확인 사항")
- [X] 11. 유니 인격: `persona.md`, `yuni` 서비스의 한마디(LLM), 고정 문구(`core/fallback_messages.py`) — 구현 완료(2026-10-19, 밝은 반말). 실기 미확인: 동기화 리포트·목표 알림·업데이트 알림의 한마디가 말투대로 나오는지, 한마디 사용량이 `/관리 사용량`에 잡히는지, 멘션 응답 말투
- [X] 12. 메모리와 관계 — 세 부분으로 나눔(2026-10-19), 모두 구현 완료
  - [X] Part 1: 메모리 전체. 호칭 칸(`player_memory.nickname`)과 호칭 우선순위(`core/address.py`), 자동 요약(5분 대기 목록 `player_memory_pending`, `YuniService.merge_memory`), `remember`의 `nickname`, 요약 반영 반응(📝), 표시물 `private`(멘션에서 DM), `memory` 스킬(`view`·`forget`·`set_auto`). 상세는 `00-core.md` "플레이어 메모리"와 `memory.md` "구현 결정". 실기 미확인: "호칭은 ○○로 해 줘" 후 호칭 사용, 5분 뒤 📝 반응과 메모리 반영, `/메모리 보기`(본인에게만)·멘션으로 "뭐 기억해?"(DM), `/메모리 지우기` 확인 버튼, 지운 항목이 다음 요약에서 되살아나지 않는지, `/메모리 자동저장` 끄면 요약이 안 도는지, 동기화 리포트 한마디에 호칭이 들어가는지
  - [X] Part 2: 대화 상태와 `yuni-bond`. 펼친 스킬의 답장 사슬 유지(`conversation_skills` 테이블, 동시 5개 상한), 대화 스레드를 `on_message`·유휴 점검(60초 폴링)·종료에 연결하고 닫힐 때 메모리 요약 대기 목록에 전문 등록(`YuniService.summarize_conversation`은 없앰), 코어가 `@prompt_context`를 프롬프트에 넣음, `yuni-bond`(테이블 2개, 도구 5개, 이벤트 4개 수신, `chunithm.plan.requested` 발신, 슬래시 `/기록들`·`/오늘계획`·`/약속`, 설정 2개). 상세는 `00-core.md` 구현 결정(Part 2)과 `yuni-bond.md` "구현 결정". 올 저스티스 첫 달성은 램프 변화 자료가 없어 뺌. 실기 미확인: 유니 답장에 답장했을 때 펼친 스킬이 이어지는지(로그의 도구 호출 수), 동기화 뒤 리포트 한마디에 마일스톤·첫 SSS·오랜만 귀띔이 들어가는지, `/기록들`·`/오늘계획`(To-Do·메모·채보 링크)·`/약속`, "다음엔 크로노 연습할게"로 약속 저장 후 다음 멘션에서 유니가 한 번 꺼내는지, 약속 속 곡을 동기화로 갱신하면 약속이 닫히는지, `/설정`에 "동기화 때 덧붙이는 말"이 보이고 끄면 귀띔이 빠지는지
  - [X] Part 3: `session`의 후기. `review_open`(`/후기`)·리포트 뒤 후기 질문·`session_reviews` 저장·`chunithm.session.closed`, 코어에 DM 대화 열기(`NotifyService.open_dm_thread`)·대화 안 지침(`ThreadSession.guidance`)·`end_conversation` 도구(말로 끝내기)·끝내기 버튼(`core/thread_view.py`)·유니 발언 전문 기록. 스레드가 아니라 DM에서 연다(2026-10-19 사용자 결정, `session.md` "구현 결정 (2026-10-19, 12단계 Part 3)"). 실기 미확인: 새 플레이 동기화 뒤 리포트 다음에 DM으로 후기 질문이 오는지, DM에 답하면 멘션 없이 짧게 답하는지(한두 문장), "끝났어"에 마무리 말과 함께 닫히는지·애매한 말에는 되묻는지, 끝내기 버튼(본인만, 누르면 버튼 사라짐), 5분 무응답 뒤 "다 끝났나요?"·다시 5분 뒤 닫힘, `/세션 보기`에 후기가 붙는지(길면 잘림), `/후기`로 지난 날짜 후기 열기, `/설정`의 "후기 물어보기"·"후기 무응답 확인 시간"·"후기 종결 시간", 대화 중 봇을 끄면 후기가 중단으로 저장되는지, 후기 대화에서 한 약속이 `yuni-bond`에 저장되는지
13~16단계는 2026-10-20에 계획 밖에서 더한 것이다. 상세와 실기 미확인 목록은 `docs/plan/2026-10-20_수정.md`의 같은 번호 절에 있다.

- [X] 13. `settings`(인게임 설정 조회·변경, 즐겨찾기) — `2026-10-20_수정.md` §13
- [X] 14. `folders`(채보 폴더) — `2026-10-20_수정.md` §14
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
  - [X] Part 2: 첫 범위 스킬(곡 검색 4개·`best`·`add_scores`·폴더 보기/저장) — 구현 완료(2026-10-20). 실기 미확인: 멘션에서 표가 실제로 붙고 11행 이상이 .txt로 나가는지, "그중에…"·"2번" 후속 요청, 곡 검색 표에 `add_scores`·`save_table`을 이어 쓰는 흐름, 동기화 뒤 옛 값 표가 거절되는지, `/상수`·`/베스트` 슬래시 출력 모양. 세부
    - [X] 2-1 공통 칸 `services/game/table_columns.py` — 구현 완료(2026-10-20)
    - [X] 2-2 `song` 곡 검색 4개 표로(`find_songs`의 `sort_by`·`descending` 제거, `find_by_artist`에 `difficulty`) — 구현 완료(2026-10-20)
    - [X] 2-3 `report` `best` 표 + `add_scores` — 구현 완료(2026-10-20)
    - [X] 2-4 `folders` `show_folder` 표 + `save_table` — 구현 완료(2026-10-20)
    - [X] 2-5 `sync` `mark_stale` + `core/bot.py` `partial_row_hints` 연결 — 구현 완료(2026-10-20)
    - [X] 2-6 `rules.md` 정리·문서 — 구현 완료(2026-10-20)
  - [X] Part 3: 그림 표 `image`(`table_image`, `ImageCache`·`jacket_url`, `SongRecord.jacket`, Pillow, Dockerfile `fonts-noto-cjk`) — 구현 완료(2026-10-20). 실기 미확인: "그림으로" 요청에서 모델이 `table_image`를 고르는지·PNG 첨부와 글자 표 중복 여부, Docker 이미지의 한글·일본어 글꼴, 캐릭터 그림·프레임 겹침(동기화 저장분), 첫 50장 받는 시간. **2026-10-21 실기 피드백 반영**: 카드 재배치·글자 1.5배·레이팅표 머리말(가동 버전 `SongDbService.current_version()`·최종 레이팅 오른쪽 끝). 실기 미확인: 재동기화 뒤 캐릭터·레벨이 뜨는지, 머리말에 가동 버전이 뜨는지. **램프 병합 수정(2026-10-21)**: 재동기화 뒤 베스트·신곡 곡의 FC/AJ가 제대로 뜨는지. 상세는 `docs/design/skills/image.md`
  - **비용·안정성 대책(2026-10-21)**: 스킬 `triggers` 사전 펼침 + `SKILL_LOADING_MODE=full`, 캐시를 10%로 센 토큰 상한·모델별 사용량 기록, `UserFacingError`와 CHUNITHM-NET 오류 코드 안내, 웹 검색 재시도·시간 상한. 상세는 `progress/2026-10-21.md` 마지막 절과 `docs/design/00-core.md` "스킬 불러오기"
  - **세션 리포트 누적·별칭 전역·이름 확인(2026-10-21)**: 같은 날 동기화는 하루치 리포트로(`records_session_state`), 후기는 요청할 때만, 모든 별칭 봇 전역(`terms_aliases`·`register_arcade`·곡 별칭), 못 알아들은 이름은 짐작해 되묻고 확인받으면 저장, "계정"=세가 계정. 상세는 `progress/2026-10-21.md` 마지막 절
  - [X] Part 4: 나머지 목록 도구를 표로(15개)·`omittable` 정리 — 구현 완료(2026-10-20). 실기 미확인: 각 목록이 멘션에서 표로 붙는지(11행 이상은 .txt)·슬래시 출력 모양(id가 같은 줄 끝 `id …`로 바뀜), `/최근`의 판정 줄, "2번 목표 삭제" 같은 말에 모델이 id를 지어내지 않고 슬래시 명령으로 안내하는지(Part 6 전까지 id 도구는 대화로 못 씀), `new_songs`·`history`의 기본 출력 가독성. 세부는 각 스킬 설계서의 "표로 바꾼 읽기 도구" 절
  - [X] Part 5: 표를 받아 칸을 더하거나 곡 행을 채보로 펼치는 도구(6개) — 구현 완료(2026-10-20). `charts_of`·`add_borders`·`add_remaining`·`add_notes`·`add_links`·`analyze(table)`(`/분석`은 `analyze_slash`로 입구를 나눔). 실기 미확인: 곡 표→`charts_of`→점수/보더/링크 붙이기 연쇄를 모델이 잇는지, 폴더 표에 `analyze(table)`, 곡 표에 `add_notes`
  - [X] Part 6: id 입구 나누기(슬래시 전용 + 표 받는 모델 도구)와 표로 데이터 바꾸기 — Part 1~5 뒤. **6-1 구현 완료(2026-10-20)**: `goals`(`delete_goals`·`update_goal`·`goal_progress`·`complete_todos`)·`yuni-bond`(`close_promises`). 실기 미확인: "2번 목표 지워줘"·"크로노 목표 지워줘"·"투두 다 완료"·"그 약속 지켰어"가 id를 지어내지 않고 표로 가는지. **6-2 구현 완료(2026-10-20)**: `folders`(`add_to_folder`·`remove_from_folder`, 조건 일괄 도구 제거)·`goals`(`add_todos`, `todo_bulk` 제거)·`notes`(`delete_notes`)·`settings`(`add_favorites`). 실기 미확인: 검색→`charts_of`→`add_scores`→`table_query`→`add_todos` 연쇄, 폴더 일괄 추가/삭제, 즐겨찾기 일괄 추가, 메모 일괄 삭제
- [ ] 17. maimai DX 국제판 확장(게임 매개변수·게임 프로필, 스킬 이름 게임 중립화, aime 쿠키 공유, otoge-db 곡 DB) — `2026-10-23_수정.md` §17
  - [X] Part 1: 게임 공통화와 스킬 이름 바꾸기(츄니즘 동작 변화 없음) — §17.3. 네 덩어리로 나눔(2026-10-23), 모두 구현 완료. 실기 미확인은 1-1 줄에 있고, 1-4: 재배포 뒤 로그인 상태(`sega-linked` 태그)가 유지되는지·쿠키 만료 알림이 오는지
    - [X] 1-1 스킬 이름 바꾸기(`chunithm-X` → `X`, `chunithm-account` → `sega-account`, `arcade`·`settings`·`updates` 포함, `chunithm-border`만 그대로) + 코어 마이그레이션 022로 저장된 이름 갱신 — 구현 완료(2026-10-23). 실기 미확인: 재배포 뒤 기존 설정(스킬 끄기·서버별 값)·예약·마이그레이션 기록이 그대로인지, 멘션 라우팅(도구 이름이 `song_…`처럼 바뀜)이 이전과 같은지
    - [X] 1-2 서비스 폴더 정리(공통 모듈 6개를 `services/game/`로)와 게임 프로필 자료형·등록부·builder — 구현 완료(2026-10-23). `core/bot.py`는 builder와 `choose_game`만 안다
    - [X] 1-3 `RecordsService`·`SongDbService`가 `game`/프로필을 받음, 게임 고르기 함수 `choose_game`을 실행기 `invoke`에 연결(`ctx.game`) — 구현 완료(2026-10-23). `TermsService`·NET은 쓰는 곳이 생기는 Part 2~5에서 `game`을 받는다. 주 게임 저장소는 Part 2
    - [X] 1-4 코어 이름 정리(`chunithm-linked` → `sega-linked`, `chunithm.login.expired` → `sega.login.expired`, manifest `game:` → `games:`; 코어 마이그레이션 023이 저장된 태그를 옮김) — 구현 완료(2026-10-23). 슬래시 `게임` 선택 인자는 `game` 인자를 받는 도구가 생기는 Part 6에서 붙인다
  - [X] Part 2: DB 마이그레이션 — 구현 완료(2026-10-23). 코어 024(`records_pb` 기본 키에 `chart_type`, `dx_score`)·025(`songdb_aliases` 게임 칸)·026(쿠키 테이블 → `sega_cookies`)·027(`players.main_game`), 스킬 `folders`·`notes`·`yuni-bond`의 002(`chart_type`을 키에 넣음, 2026-10-23 사용자 결정으로 `yuni-bond`도 같이). 운영 DB 사본에 적용해 행 수·외래키 확인. `chart_type`·`dx_score`·`main_game`을 채우고 읽는 코드는 Part 5·6 — §17.4
  - [ ] Part 3: aime 인증 공통화와 maimai DX NET 어댑터 — §17.5. 시작 조건: SEGA 사이트 복구 후 실측
  - [X] Part 4: maimai 곡 DB(otoge-db) — 구현 완료(2026-10-23). `services/game/songdb.py`(곡·채보 자료형·찾기 공통 틀, `chart_type` 조건)에서 츄니즘 `SongDbService`와 `services/maimai/songdb.py`의 `MaimaiSongDbService`가 갈라진다. 봇 부팅·등록부 연결은 아직 없다(Part 6) — §17.6
  - [X] Part 5: 기록·레이팅·동기화 — 구현 완료(2026-10-24, NET 없이 가능한 범위). `RecordsService`가 게임 중립 `SyncSnapshot`(`services/game/sync_types.py`)을 받고, 채보 키는 `(제목, 채보 종류, 난이도)`, `records_pb`에 `chart_type`·`dx_score`를 읽고 쓴다. 레이팅 규칙·램프 순서·첫 동기화 태그는 `GameProfile`에서 받고(maimai 레이팅: `services/maimai/rating.py`), 베스트/신곡 목록이 없으면 전체 기록으로 프레임을 짠다. 남은 것: maimai 파서·어댑터가 `SyncSnapshot`을 채우는 일(Part 3), 계산 레이팅과 NET 표시 레이팅 대조(실기), 스킬 쪽 `chart_type` 전달(Part 6) — §17.7
  - [ ] Part 6: 스킬 확장과 프롬프트 — §17.8. 결정은 2026-10-24에 끝냈다(§17.8 "정리한 범위와 순서", §17.9 1번 = A안)
    - [X] 6-1 서비스 배선 — 구현 완료(2026-10-24). 용어 사전을 게임별로(`TermsService`가 `services/game/terms.py`로, 마이그레이션 028, `services/maimai/data/terms.yaml`), 게임별 서비스 한 벌(`build_all_game_services`)과 해석기 분배(`_ByGame`), 실행기가 고른 게임에 맞춰 서비스를 바꿔 끼움·스킬이 안 다루는 게임이면 스킬의 게임으로, maimai 곡 DB 부팅·주기 갱신, `admin`의 `set_song_version`, 등록부에 maimai 등록
    - [X] 6-2 `GameRef`(표준 인자 타입, `resolve_game_name`이 등록 이름·표시 이름·별칭을 받는다)와 도구별 `games` 제한(`@tool(games=[...])`) — 구현 완료(2026-10-24). 슬래시 옵션은 `GameRef` 인자를 가진 도구에 자동으로 생긴다(옵션 이름은 인자 이름 `game`, 다른 옵션처럼 영문). `game` 인자를 실제 도구에 붙이는 일은 스킬별 작업(6-4·6-5)에서
    - [X] 6-3 주 게임을 `/설정 바꾸기 skill:player setting:주 게임 value:마이마이`로(스킬 설정이 아니라 `players.main_game`에 저장, `/설정 보기`에도 나옴), 실행기가 게임 고르기 ②번으로 읽음 — 구현 완료(2026-10-24)
    - [ ] 6-4 `song`·`image`의 maimai 대응(`ChartRef`의 채보 종류 포함)
    - [ ] 6-5 기록 의존 스킬(`report`·`goals`·`session`·`folders`·`notes`)
    - [ ] 6-6 플레이어 메모리의 게임별 구획
    - [ ] 6-7 프롬프트(`persona.md` 소개 문장, `rules.md` 게임 중립 말, 카드 예시)

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
