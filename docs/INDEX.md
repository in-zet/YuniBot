# 경로 색인

상태 태그: **관리대상**(계속 갱신됨) · 보존(참고용, 삭제 안 함) · 재생성 가능(언제든 지워도 됨) · **읽기전용**

문서를 어디에 쓸지는 `docs/doc-guide.md`를 따른다.

| 경로 | 설명 | 상태 |
|---|---|---|
| `CLAUDE.md` | 세션마다 자동 로드 — 스택·범위·제약 요약 + 문서 포인터 | **관리대상** |
| `docs/INDEX.md` · `docs/doc-guide.md` | 이 문서 · 문서 관리 지침 | **관리대상** |
| `docs/start.md` / `docs/end.md` | 세션 시작·종료 체크리스트(검증 명령은 `end.md` §1) | **관리대상** |
| `docs/plan.md` | 범위 · 작업 단계 · 결정 기록 · 리스크 | **관리대상** |
| `docs/plan/` | 날짜별 계획 수정(`YYYY-MM-DD_수정.md`) — 계획 밖에서 더한 단계의 상세. `plan.md` §4가 단계마다 가리킨다 | **관리대상** |
| `docs/conventions.md` · `docs/pitfalls.md` | 개발 규칙 · 겪은 함정 | **관리대상** |
| `docs/design/` | 기준 설계서: `00-core.md`(코어) + `skills/<스킬>.md` 19개 | **관리대상** |
| `docs/reference/` | CHUNITHM-NET 페이지·Chunimaru 응답 저장본 — 파서 시험이 읽는다 | **읽기전용** |
| `docs/persona/yuni-lines/` | 유니 일본어 대사 원문: `uni/`(유니)·`navi/`(나비)·`setting/`(설정 안내문). 말투 튜닝 자료, 봇은 읽지 않는다 | **관리대상** |
| `skills/yuni-lore/lore.md` | 유니 설정집(우리말, 구간·스포일러 구분). `yuni-lore` 스킬의 `read_lore`가 읽는다 | **관리대상** |
| `progress/` | 날짜별 작업 일지(`YYYY-MM-DD.md`) — 실제 작업의 유일한 확정 기록 | **관리대상** |
| `core/` | 입구·에이전트·도구 실행기·레지스트리·이벤트·예약·LLM 어댑터 | **관리대상** |
| `services/common/` · `services/chunithm/` | 공통 서비스 · 츄니즘 서비스(`data/terms.yaml`·`data/linked_verse.yaml` 포함) | **관리대상** |
| `skills/` | 스킬 폴더(`SKILL.md`·`skill.py`·`migrations/`·`tests/`), `_template/` | **관리대상** |
| `prompts/` | `rules.md`(규칙) · `persona.md`(말투) — 모델에 그대로 들어간다 | **관리대상** |
| `migrations/core/` | 코어·서비스 SQL. 적용한 파일은 고치지 않는다 | **관리대상** |
| `scripts/` | `sync_upstream.py`(third_party 교체) · `eval_agent.py`(도구 선택 평가, LLM 키 필요) · `clear_global_commands.py`(옛 전역 슬래시 정리) | **관리대상** |
| `tests/` | 코어·서비스 시험, `fixtures/`(연습·불량 스킬), `support/fake_llm.py` | **관리대상** |
| `third_party/chuni_penguin/` | chuni-penguin에서 가져온 코드 + 시드. `database/seeds/songs.json`(7.5MB)은 열지 않는다 | **읽기전용** |
| `third_party/patches/` | 가져온 코드에 자동 적용하는 패치와 이유 | **관리대상** |
| `.claude/settings.json` | 프로젝트 Claude Code 설정 — `songs.json` 읽기 차단 | **관리대상** |
| `Dockerfile` · `docker-compose.yml` · `pyproject.toml` · `uv.lock` · `.env.example` | 빌드·운영·의존성·환경 변수 틀(값 없음) | **관리대상** |
| `data/` | 운영 DB·캐시·로그(`yuni.db`, `http_cache.sqlite`, `images/`(재킷·캐릭터 그림 캐시, 지워도 안전), `logs/yuni.log` — 5MB씩 6개 돌려 씀) — git 제외 | 보존(운영) |
| `temp/` | 임시 파일·컨텍스트 스냅샷 — git 제외 | 재생성 가능 |
