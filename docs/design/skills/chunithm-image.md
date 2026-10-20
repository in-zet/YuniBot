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
  - `card`: 카드 176px, 재킷 160px(원본 190px보다 크게 늘리지 않음), 가로 최대 5장. 재킷 위에 `#번호`와 난이도 칩. 아래에 제목(두 줄까지), 상수, 점수, 랭크·램프·곡 레이팅. 표에 있는 칸만 그린다.
  - `list`: 한 줄에 `#번호`·재킷·제목·난이도·상수·점수·랭크·램프·곡 레이팅.
  - 곡 단위 행(난이도 빈 값)은 있는 칸만 그리므로 재킷과 제목만 남는다.
  - 머리말: 닉네임(표 요약의 `닉네임`, 없으면 `latest_sync_summary`)·레벨(`records.player_profile`)·캐릭터 그림·프레임(주소는 프로필에 저장돼 있다, 그릴 때 받아 캐시), 나머지 요약, `steps`. `subject`가 없으면 요약과 `steps`만. 옛 값 표면 "동기화 전 값" 표시.
  - 나누기: 카드 50·리스트 30행. 묶음 경계에서 자르고, 큰 묶음은 그 안에서 자르며 이어지는 조각의 띠에 "(이어서)". 쪽이 여럿이면 파일 이름에 `_1`, `_2`와 머리말에 `n/N쪽`.
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
