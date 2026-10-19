# 개발 규칙

구조·DB·시험 규칙. 하드 제약은 `CLAUDE.md`, 코어 규격은 `docs/design/00-core.md`가 기준이다.

## 1. 폴더와 의존 방향

```
core/                  입구, 에이전트, 도구 실행기, 레지스트리, 메모리, 예약, 이벤트, LLM 어댑터
services/common/       players, settings, memory, notify, yuni, secrets, arcades, http (websearch는 10단계)
services/chunithm/     chunithm_net, songdb, records, terms
skills/<스킬>/         SKILL.md(manifest+지침) · skill.py · migrations/ · tests/
skills/_template/      새 스킬의 복사본
prompts/               rules.md(규칙), persona.md(유니 말투)
third_party/           chuni_penguin(가져온 코드, 읽기 전용) · patches/
migrations/core/       코어와 서비스의 SQL 파일(번호순)
scripts/               sync_upstream.py, eval_agent.py, clear_global_commands.py
tests/                 코어·서비스 시험(스킬 시험은 각 스킬의 tests/)
```

- 코어는 츄니즘을 모른다. 츄니즘 지식은 `services/chunithm/`과 `chunithm-*` 스킬에만 둔다.
- 스킬은 다른 스킬을 import하지 않는다. 스킬끼리는 서비스와 이벤트로만 주고받는다.
- 스킬은 manifest의 `services`에 적은 서비스만 `ctx`로 쓴다. 다른 스킬의 테이블과 설정은 읽지 않는다(그 스킬 문서가 다른 스킬의 재사용을 명시한 설정은 예외 — `00-core.md` "데이터, 권한, 비밀 값" 참고).
- 새 스킬은 `skills/_template/`을 복사해 만든다. 스킬을 더하려고 코어나 다른 스킬을 고쳐야 한다면 규격이 모자라다는 뜻이므로 규격부터 고친다. 서비스에 메서드를 더하는 것은 정상 경로다.
- 스킬을 설치에서 빼는 것은 설정(`SKILLS_DIRS`)으로 하고 재시작하면 반영된다. 서버별 켜고 끄기는 `/관리 스킬설정`(DB, 바로 반영).

## 2. third_party

- `third_party/`는 직접 고치지 않는다. 원본이 바뀌면 `scripts/sync_upstream.py`로 통째 교체하고 `UPSTREAM` 파일에 원본 커밋을 남긴다.
- 꼭 필요한 작은 동작 차이는 `third_party/patches/*.patch`로 보관한다. `sync_upstream.py`가 교체 뒤 자동 적용하고, 패치가 안 맞으면 스크립트가 실패한다. 패치별 이유는 `third_party/patches/README.md`.
- 원본 어댑터가 요구하는 라이브러리와 버전은 그대로 따른다. `hishel`은 `==0.1.3` 고정이다(1.x는 내부 모듈 구조가 바뀌어 vendored 어댑터가 깨진다). 원본이 SQLAlchemy를 import하면 `third_party` 때문에만 의존성으로 두고 우리 코드에서는 쓰지 않는다.
- `ruff`와 `basedpyright`는 `third_party/`를 보지 않는다(`pyproject.toml`에서 제외). 시험과 타입 검사가 vendored 코드를 import하도록 `third_party`가 추가 경로에 들어 있다.

## 3. DB와 마이그레이션

- SQLite + aiosqlite. ORM과 alembic은 쓰지 않고 SQL 문을 직접 쓴다.
- 테이블은 `migrations/NNN_설명.sql` 파일로 만든다. 코어(`migrations/core/`)가 먼저, 스킬은 이름순으로 실행하고 `schema_migrations`에 기록한다.
- 한 번 적용한 SQL 파일은 고치지 않는다(체크섬으로 잡혀 기동이 멈춘다). 바꿀 것이 있으면 다음 번호 파일을 더한다.
- 스킬 테이블 이름은 스킬 이름에서 `chunithm-`을 빼고 하이픈을 밑줄로 바꾼 접두사로 시작한다(`chunithm-goals` → `goals_`, `yuni-bond` → `yuni_bond_`).
- 게임에 딸린 테이블에는 `game` 열을, 플레이어 데이터에는 `player_id` 열을 둔다.

## 4. 시험

- 계산 로직은 순수 함수로 두고 시험을 먼저 쓴다. 레이팅, 허용 어택, 목표 달성률, 세션 날짜 배정이 해당한다.
- 계약 시험은 모든 스킬에 자동으로 돈다: manifest, 도구 스키마, 이름과 슬래시 중복, docstring, 선언하지 않은 서비스 사용, 테이블 접두사, 마이그레이션 파일 이름.
- 도구 함수의 docstring은 모델에게 가는 도구 설명이다. 계획·이력 이야기를 쓰지 않는다.
- CHUNITHM-NET 파서는 `docs/reference/`의 저장본으로 시험한다. 실제 사이트에 접속하는 시험은 만들지 않는다.
- LLM을 부르는 시험은 기본 실행에서 뺀다. 스킬 `examples`로 도구 선택을 재는 평가는 `scripts/eval_agent.py`(LLM 키 필요).

## 5. 주석

- 주석은 지금 코드가 왜 이런지만 적는다. 계획 단계 번호("Task 7"), 리뷰 번호("I-3"), 수정 이력("~전에는 ~였지만")은 쓰지 않는다 — 그런 근거는 `progress/`에 남긴다.
- 설계서의 12단계 번호("9단계에서 더한다")는 `docs/plan.md` §4에 남아 있으므로 써도 된다.
