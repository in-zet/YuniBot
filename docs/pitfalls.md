# 함정 모음

실제로 겪은 것만 적는다. 항목당 한두 줄 + 대응.

## 도구·환경

- **basedpyright 출력 폭주**: `uv run basedpyright`를 그냥 돌리면 경고 4천여 개(약 650KB)가 출력된다(2026-10-18 실측). → 항상 `--level error`.
- **AskUserQuestion 한글 깨짐**: 구조화된 도구 인자에 한글을 넣었다가 글자가 깨진 적이 있다. → 보내기 전에 question/header/option 텍스트를 다시 읽어 확인한다.
- **Windows에서 시험 수집 실패**: 기본 인코딩(cp949) 때문에 `read_text()`가 깨지고, 시간대 데이터가 없어 `zoneinfo`가 실패한다(2026-10-18, 운영 대상은 리눅스라 코드 문제는 아님). → Windows에서는 `PYTHONUTF8=1 uv run --with tzdata pytest -q`.

## 외부 서비스

- **Gemini 503**: 모델 과부하 때 `503 UNAVAILABLE`이 오고, 어댑터가 재시도하지 않아 바로 "지금 응답을 만들 수 없다"로 끝난다. → 로그 `멘션 처리 실패`의 traceback으로 확인(`docker compose logs yuni | grep -A30 "멘션 처리 실패"`). 대응은 `docs/plan.md` §5.
- **Chunimaru 호출 제한**: IP당 시간당 30회. → 저장본을 다시 받을 일이 있으면 한 번만 호출한다(`docs/reference/chunimaru/README.md`).
- **CHUNITHM-NET 베스트30/신곡20 PB는 `song.id=0`**: 채보별 상세 조회(판정 수)를 할 수 없다. 판정 수는 최근 플레이 항목에서만 상세 조회로 얻는다.
- **베스트30/신곡20 파서는 `achieved_at`을 채우지 않는다**: 플레이 날짜는 최근 플레이 이력(`parse_basic_recent_record`)에만 있다. 세션 날짜는 그걸로 정한다.
- **국제판 공지 `news--date`는 월/일을 0으로 안 채운다**("2026.9.30", "2026.10.15") — 요일 span도 공백 없이 바로 붙는다("2026.9.30Wed"). 글자 수로 자르면 한 자리 월에서 깨진다. → 날짜 패턴을 정규식으로 뽑는다(`updates_calc._INTL_POSTED_DATE_RE`).
- **일본판 공지 제목의 『』는 버전명 전용이 아니다**: 콜라보 이벤트 이름에도 쓴다(저장본의 "『いよわ』リバイバルイベント開催"). 국제판만 믿을 수 있다(`docs/design/skills/chunithm-updates.md` "구현 결정").
- **스킬 `skill.py`에서 `@dataclass`를 쓰면 로드가 실패할 수 있다**: `from __future__ import annotations`와 함께 쓰면 `dataclasses`가 `sys.modules`에서 모듈을 찾는데, 레지스트리가 불러온 모듈은 등록돼 있지 않아 `NoneType has no attribute __dict__`로 "스킬 로드 실패"가 난다(`yuni-lore`, 2026-10-20). 단위 시험은 `sys.modules`에 직접 넣어 통과해도 `tests/core/test_contract.py`가 잡는다. → 단순한 값 묶음은 `NamedTuple`로 쓴다.
