# 유니 스킬: chunithm-image

Oct 20, 2026 · @astrove

작업 표(`core/tables.py`)를 재킷이 들어간 그림(PNG)으로 그려 주는 스킬이다. 기준 설계는 `docs/design/00-core.md` "그림 표"이고, 이 문서는 구현 결정만 적는다.

## 개요

```yaml
name: chunithm-image
description: 방금 만든 표(곡 검색 결과, 베스트, 폴더 등)를 재킷이 들어간 그림으로 그려 준다. 레이팅표, 상수표처럼 그림으로 보고 싶을 때 쓴다.
category: 기록과 리포트
game: chunithm
version: 1
weight: heavy
services: [songdb, records, image_cache, tables]
```

## 도구

- `table_image(table, layout: card | list = card)` — 슬래시 없음, `mutates` 아님. `table`은 `Annotated[TableRef, TableNeeds(nonempty=[SONG])]`라 곡 칸이 빈 행은 실행기가 뺀다.
- 결과: `Display.files`에 PNG(쪽마다 한 장), `ToolResult.drew_table`에 표 id(코어가 그 표의 글자 자동 출력을 건너뜀), `data`에 `table`·`images`·`rows`·`missing_jackets`.
- 표를 거르거나 정렬하거나 묶지 않는다. 묶음·표지·요약·행 번호는 표가 가진 그대로 그린다.

## 구현 결정 (2026-10-20, 작업 표 Part 3)

- **서비스 이름**: 설계는 `services: [songdb, records, http]`였다. 그림 받기와 디스크 캐시를 `services/chunithm/image_cache.py`의 `ImageCache`(서비스 이름 `image_cache`, `core/bot.py`에서 `data/images/`로 만든다)로 묶어, 스킬은 `image_cache`를 쓴다. `http` 서비스는 `ImageCache`가 안에서 쓴다. 옛 값 표시를 알아내려고 `tables`도 선언한다(`ctx.tables.is_stale`).
- **재킷 주소**: `image_cache.jacket_url`. 파일 이름이면 국제판 `INTERNATIONAL_JACKET_BASE`, 일본 시각 4~7시에는 `JACKET_BASE`(chuni-penguin `constants`). 이미 `http(s)://`로 시작하면 그대로 쓴다.
- **`SongRecord.jacket`**: 시드의 `jacket` 값(파일 이름). Chunimaru 덧씌우기가 시드에 없던 곡을 만들 때나, 시드에 재킷이 없던 곡에는 Chunimaru가 준 전체 주소를 넣는다(설계에는 없던 보강 — 최신 곡의 재킷이 빈 사각형이 되지 않게). 시드에 값이 있으면 덮어쓰지 않는다. `songdb.jacket_for(title)`로 꺼낸다.
- **캐시와 받기**: 파일 이름은 주소의 마지막 부분. 디스크에 있으면 받지 않는다. 받은 바이트는 Pillow로 그림인지 검사한 뒤에만 저장한다(HTML 오류 페이지가 영구 캐시되지 않게). 실패한 그림은 None(빈 사각형)이고 캐시하지 않는다. 같은 주소는 한 번만 받고 동시에 8개까지.
- **호출 간격**: `HttpService.get(url, *, min_interval_seconds=None)`을 더해 그림 받기만 호스트별 0.1초 간격으로 센다(기본 1초면 50장에 50초). 한도 계수기는 (호스트, 간격)별이다.
- **그리는 코드**: `skills/chunithm-image/render.py`(입출력 없음, 그림 바이트를 받아 PNG를 돌려준다). `skill.py`가 songdb·records·image_cache로 재료를 모은다.
  - `card`: 카드 206px, 재킷 190px(원본 크기, 더 늘리지 않음), 가로 최대 10장(표가 작으면 3~9장). 저장할 때 `CARD_OUTPUT_SCALE`배로 줄일 수 있다(지금은 1 = 줄이지 않음). 재킷 위쪽에 왼쪽부터 `#번호`·램프 칩, 오른쪽 위에 난이도 칩. 곡명은 재킷 안쪽 아래에 흰 글씨(두 줄까지)로 넣고 그 자리에 검은 그라데이션을 깐다. 재킷 아래는 두 줄: 첫 줄 `상수`(왼쪽)·곡 레이팅(오른쪽 끝), 둘째 줄 점수(왼쪽)·랭크(오른쪽 끝). 표에 있는 칸만 그린다.
  - `list`: 한 줄에 `#번호`·재킷·제목·난이도·상수·점수·랭크·램프·곡 레이팅.
  - 곡 단위 행(난이도 빈 값)은 있는 칸만 그리므로 재킷과 제목만 남는다.
  - **카드 색(2026-10-21 결정)**: 카드 바탕에 난이도 색을 22% 섞는다(`_DIFFICULTY_TINTS`, ULT는 섞지 않고 어두운 붉은 바탕 `_ULT_PANEL`). 램프(FC·AJ·AJC)가 있으면 카드에 2px 테두리를 램프 색으로 두른다(AJC 금색·AJ 주황·FC 파랑, 칩과 같은 색). 랭크 글자색은 D~SSS+ 14단계가 모두 다르다(`_RANK_COLORS`: 낮은 쪽 흐림 → B 파랑 → A 초록 → S 은색 → SS 주황 → SSS 금색 → SSS+ 백금, 목록 배치도 같다). 재킷 아래 검은 그라데이션은 곡명 줄 위로 40px까지 깔린다(예전 22px, 80은 과해서 40으로).
  - **글자 크기 1.5배(2026-10-21)**: `FONT_SCALE`을 `_font`가 적용한다. 머리말·카드·목록 모두. 카드의 상수·곡 레이팅·점수·랭크는 글자 크기를 하나로 통일한다(2026-10-21 결정, 점수 크기 기준): 가장 긴 점수+랭크 줄과 상수+곡 레이팅 줄이 카드 폭에 들어가는 가장 큰 크기(17→14, 안 되면 13).
  - 머리말: 닉네임(표 요약의 `닉네임`, 없으면 `latest_sync_summary`)·레벨(`records.player_profile`)·캐릭터 그림·프레임(주소는 프로필에 저장돼 있다, 그릴 때 받아 캐시), 나머지 요약, `steps`. `subject`가 없으면 요약과 `steps`만. **레이팅표**(요약에 `레이팅`이 있는 표)만 머리말 오른쪽 끝에 최종 레이팅을 크게 그리고(요약 줄에서는 뺀다), 작은 줄 맨 앞에 가동 버전을 둔다. 가동 버전은 `SongDbService.current_version()`(신곡 국제판 곡들의 수록 버전 중 최빈값, 2026-10-21 결정 — 설정값 대신 곡 DB에서 뽑는다, 버전업 때 손볼 곳이 없다). 옛 값 표면 "동기화 전 값" 표시.
  - **번호(2026-10-21 결정)**: 묶음마다 1부터 다시 센 번호를 `#3`로 찍고, 표 번호와 다르면 `#3 (33)`처럼 병기한다(카드는 띠 안에 작게, 목록은 아래 줄). 묶음이 쪽 경계에서 잘려도 이어서 센다. 행을 고르는 코드는 표 번호만 받는다.
  - 나누기(2026-10-21 결정): 카드는 개수가 아니라 줄로 세어 한 장에 8줄(10열이면 80장)까지, 묶음마다 새 줄에서 시작한다. 리스트는 30행. 묶음 경계에서 자르고, 큰 묶음은 그 안에서 자르며 이어지는 조각의 띠에 "(이어서)". 쪽이 여럿이면 파일 이름에 `_1`, `_2`와 머리말에 `n/N쪽`.
  - 색(난이도·랭크·램프)은 이 스킬에 둔다. 디자인은 다른 사이트 것을 베끼지 않았다.
- **글꼴**: Docker 이미지에 `fonts-noto-cjk`. 코드는 `NotoSansCJK-Regular/Bold.ttc`(한국어 얼굴 1번)를 먼저 찾고, 환경 변수 `YUNI_FONT_REGULAR`·`YUNI_FONT_BOLD`로 글꼴 파일을 직접 줄 수 있다(개발 PC용). 아무 CJK 글꼴도 없으면 기본 글꼴로 그리고 경고를 남긴다(한글이 깨짐).
- **Pillow 추가의 부작용**: Pillow가 깔리면 google-genai 입력 타입에 PIL 이미지가 끼어 `core/llm_gemini.py`의 `contents` 리스트가 타입 오류가 나서 `list[types.ContentUnion]`으로 타입을 적었다(동작 변화 없음).

## 테스트

- `tests/services/chunithm/test_image_cache.py`(주소 규칙·캐시·실패·중복), `skills/chunithm-image/tests/test_render.py`(쪽 나누기·번호·빈 사각형·곡 단위 행·머리말), `test_chunithm_image.py`(스킬: 파일·`drew_table`·프로필 받기·`TableNeeds`).
- 그림 내용(모양·글자 깨짐)은 사람이 실기로 본다.

## 미확인 사항

- 멘션·슬래시 없이 모델이 "그림으로" 요청에서 `table_image`를 고르는지, 멘션에서 PNG가 첨부되고 글자 표가 중복으로 나가지 않는지.
- Docker 이미지에서 `fonts-noto-cjk`로 한글·일본어 제목이 모두 나오는지(개발 PC는 Noto Sans KR 부분 글꼴로 확인했고, 일부 한자·기호가 빈 네모로 나왔다).
- 일본 시각 4~7시에 일본판 주소로 받아지는지, 실제 캐릭터 그림·프레임이 70×70 안팎 비율로 맞게 겹치는지(동기화 저장분으로).
- 첫 그림(캐시 비어 있음) 50장의 받는 시간.
