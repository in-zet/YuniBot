# 유니 스킬: chunithm-folders

Oct 20, 2026 · @astrove

채보(곡+난이도)를 모아 이름 붙인 폴더를 만들고 관리하는 스킬이다. CHUNITHM-NET과는 무관하게
이 봇 DB에만 저장하는, 플레이어 본인 전용 채보 모음이다. 난이도별·상수별 폴더뿐 아니라
아무 채보나 넣은 자유 폴더도 가능하다.

## 개요

```yaml
name: chunithm-folders
description: 채보(곡+난이도)를 모아 이름 붙인 폴더를 만들고, 채보를 하나씩 또는 조건(상수 범위·레벨·수록 버전)으로 일괄 추가/삭제하며, 폴더에 메모를 남긴다.
examples:
  - 15.0 상수 곡 모아서 폴더 만들어줘
  - 내 폴더 목록 보여줘
  - 상수 15.0 폴더에 크로노 마스 추가해줘
  - 레벨 14+ 폴더 이름을 입문용으로 바꿔줘
category: 폴더
game: chunithm
version: 1
services: [songdb, db]
```

## 왜 `chunithm-goals`의 "폴더 정의"와 다른가

`chunithm-goals`에도 "폴더"라는 말이 나오지만(표기 레벨·상수 범위·수록 버전으로 goal의
대상 범위를 정하는 것), 그건 그 자리에서 계산만 하고 저장하지 않는 **필터**다. 이 스킬의
폴더는 그와 달리 **이름 붙여 저장하는, 채보 목록 그 자체**다 — 한 번 조건으로 채운 뒤에도
개별 채보를 넣고 빼며 계속 고쳐 쓴다. 조건으로 채보를 찾는 로직(`songdb.find_by_const` 등)은
goals와 이 스킬이 그대로 공유한다(새로 안 만든다).

## 폴더

- 이름(플레이어가 자유롭게 짓거나 유니가 맥락으로 지어 줌) + 내부 PK(`id`, 사람에게 노출 안
  함) + 메모(선택) + 그 안의 채보 목록. 플레이어 안에서 이름은 유일하다(대소문자 구분 안 함).
- 모든 도구가 폴더를 **이름으로만** 가리킨다. 이름이 안 겹치므로 별도의 전역 해석기
  (`STANDARD_REF_TYPES`)를 만들지 않고, 이 스킬 안에서 `player_id` + 이름으로 바로 찾는다.
- 채보는 `(song_title, difficulty)`로 식별한다(이 저장소의 다른 스킬 — notes, goals의 To-Do
  — 와 같은 키). CHUNITHM-NET 실제 musicId(`chunithm-settings`가 쓰는 `SongRecord.id`)는
  필요 없다 — 폴더는 CHUNITHM-NET에 쓰지 않는다.
- 자동 삭제는 없다. 국제판 업데이트로 채보가 빠지거나(예: WE 교체) 폴더가 그 채보를 들고
  있어도, 이번 범위에서는 그대로 둔다(말로 지우기 전까지 유지).

## 조건 일괄 추가/삭제

- 상수 범위(`min_const`+`max_const`)·레벨(`level`)·수록 버전(`version`) 중 **정확히 하나**만
  받는다(`chunithm-goals`의 "폴더 정의"와 같은 제약 — `songdb`에 장르가 없어 장르 조합은
  지원하지 않는다). 난이도(`difficulty`)는 선택— 비우면 그 조건에 맞는 채보를 난이도 가리지
  않고 전부 찾는다(`find_by_const`의 기존 결정, 2026-10-18).
- 추가는 조건에 맞으면서 아직 폴더에 없는 채보만, 삭제는 조건에 맞으면서 지금 폴더에 있는
  채보만 건드린다 — 둘 다 멱등이다(두 번 불러도 둘째 번은 "없다"고 끝난다).
- `confirm="bulk"`: 대상이 2개 이상이면 실행기가 먼저 개수를 보여주고 확인 버튼을 받는다.
  대상 이름을 전부 나열하면 메시지가 과하게 길어질 수 있어(레벨 하나로 채보 수십~백여 개가
  걸릴 수 있다) 15개까지만 보여주고 나머지는 개수로 뭉친다.

## 이름 짓기

- 폴더 이름을 말로 받지 않았을 때(`create_folder`의 `name`은 함수 인자로는 필수지만, 유니가
  대화에서 못 받았으면 직접 지어서 채운다), 뒤따르는 생성 의도(상수 범위·레벨·수록 버전 등)로
  뜻이 통하는 이름을 짓는다 — 숫자나 조건값은 유니가 그 턴에 실제로 쓴 조건 그대로 옮긴다(코드가
  계산한 상수·레벨이 아니라 플레이어가 말한 조건 문구를 이름에 반영하는 것이라 하드 제약의
  "숫자는 코드가 계산" 규칙과 충돌하지 않는다 — 이름은 숫자 계산이 아니라 사용자가 준 문맥을
  문자열로 옮기는 것이다). 맥락이 전혀 없으면 이름을 물어본다.
- `rename_folder`로 언제든 이름을 바꿀 수 있다.

## 도구

| 함수 | 하는 일 | 인자 | 슬래시 | display | confirm |
| --- | --- | --- | --- | --- | --- |
| `create_folder` | 빈 폴더 생성 | `name: str` | `/폴더 생성` | optional | never |
| `delete_folder` | 폴더+메모+채보 통째로 삭제 | `name: str` | `/폴더 삭제` | optional | always |
| `rename_folder` | 폴더 이름 변경 | `name, new_name: str` | `/폴더 이름변경` | optional | never |
| `list_folders` | 내 폴더 목록(이름·채보 수·메모 유무) | 없음 | `/폴더 목록` | required | never |
| `show_folder` | 폴더 메모 + 안의 채보 전체 | `name: str` | `/폴더 보기` | required | never |
| `add_chart` | 채보 한 개 추가 | `name: str, chart: ChartRef` | `/폴더 채보추가` | optional | never |
| `remove_chart` | 채보 한 개 삭제 | `name: str, chart: ChartRef` | `/폴더 채보삭제` | optional | never |
| `add_by_condition` | 조건에 맞는 채보 일괄 추가 | `name, difficulty?, min_const?, max_const?, level?, version?` | `/폴더 조건추가` | required | bulk |
| `remove_by_condition` | 조건에 맞는 채보 일괄 삭제 | 위와 같음 | `/폴더 조건삭제` | required | bulk |
| `set_note` | 폴더 메모 설정/덧붙이기/지우기(빈 문자열) | `name: str, text: str, append=False` | `/폴더 메모` | optional | never |

모든 도구가 `cost="light"`(네트워크 호출 없음), `mutates=True`는 생성·삭제·이름변경·채보
추가/삭제·조건 일괄·메모(조회 넷을 뺀 전부)에 — 전부 `ctx.player_id` 본인 폴더만 건드린다
(`PlayerRef` 인자 없음).

## 이벤트

없음. 다른 스킬과 이벤트로 주고받지 않는다.

## 데이터

| 테이블 | 열 |
| --- | --- |
| `folders_folders` | `id`(PK), `player_id`, `game`, `name`(플레이어+게임 안에서 유일, 대소문자 구분 안 함), `note`, `created_at`, `updated_at` |
| `folders_charts` | `folder_id`(FK), `song_title`, `difficulty`, `added_at` — PK `(folder_id, song_title, difficulty)` |

## 설정

없음.

## 유니 지침 (SKILL.md 본문 초안)

- 이름을 안 말했으면 뒤따르는 조건으로 뜻이 통하는 이름을 짓는다. 조건도 없으면 물어본다.
- 생성 + 조건 일괄 추가가 한 번에 왔으면 두 도구를 이어서 부른다.
- 조건은 상수 범위·레벨·수록 버전 중 하나만 받는다. 둘 이상 섞으면 못 한다고 말한다.
- 목록/보기는 표가 자동으로 붙으니 다시 나열하지 않는다.

## 테스트

- 이름 중복(대소문자 다름 포함) 생성 거절, 없는 폴더 조회/삭제/수정 시 있는 폴더 이름을
  힌트로 보여주는지.
- `add_by_condition`/`remove_by_condition`이 조건 2개 이상 섞으면 에러, 이미 있는/없는 채보를
  걸러 멱등인지, `dry_run`이 실제로 바꾸지 않는지.
- `add_chart`/`remove_chart`의 중복 추가·없는 채보 삭제가 조용히 끝나는지.
- `set_note`의 덮어쓰기·append·빈 문자열로 지우기.
- `rename_folder`가 다른 폴더 이름과 겹치면 거절하는지(자기 자신으로의 "변경"은 허용).

## 미확인 사항

- 실기 미확인: "15.0 상수 곡 모아서 폴더 만들어줘"에 유니가 이름을 스스로 지어 생성 후
  일괄 추가까지 이어서 하는지, 대상이 많을 때 확인 버튼이 뜨는지, `/폴더` 슬래시 그룹의
  하위 명령이 디스코드에 정상 등록되는지.
