# 유니 스킬: chunithm-settings

Oct 20, 2026 · @astrove

지금 로그인된 계정의 CHUNITHM-NET을 직접 읽고 쓰는 스킬이다. 인게임 설정(스피드·필드 월·판정 타이밍 A·B·가이드음)과 즐겨찾기 곡을 유니에게 말로 조회하고 바꿀 수 있다. 아무것도 우리 DB에 저장하지 않는다 — 오락실별 설정 이력은 `chunithm-arcade`가 따로 맡는다.

## 개요

```yaml
name: chunithm-settings
description: 지금 계정의 인게임 설정(스피드·필드 월·판정 타이밍 A·B·가이드음)과 즐겨찾기 곡을 CHUNITHM-NET에서 바로 읽고 바꾼다.
examples:
  - 내 지금 설정 좀 보여줘
  - 스피드 9.00으로 바꿔줘
  - 즐겨찾기에 크로노마기아 추가해줘
  - 즐겨찾기 목록 보여줘
category: 오락실
game: chunithm
version: 1
services: [chunithm_net, songdb]
tags: [chunithm-linked]
```

## 왜 `chunithm-arcade`가 아니라 별도 스킬인가

`chunithm-arcade`는 "오락실별로 설정을 저장하고 꺼내 보는" 스킬이고(동기화 때 오락실을 물어 방문에 묶어 저장), 이 스킬은 "지금 계정 설정 자체를 CHUNITHM-NET에서 바로 읽고 쓰는" 스킬이다. 전자는 오락실(`ArcadeRef`)과 방문 이력이 핵심이고, 후자는 오락실 개념이 전혀 없다(즐겨찾기 곡도 오락실과 무관). 둘 다 CHUNITHM-NET의 "지금 계정 설정 페이지"(`get_arcade_settings`)를 읽지만, 그 서비스 메서드는 공유하고 스킬만 나눴다.

## 인게임 설정

설정 페이지는 `/mobile/home/userOption/updateUserOption`(GET으로 조회), `/mobile/home/userOption/updateUserOption/update/`(POST로 변경)다. 폼은 `<select>` 하나당 이름 붙은 선택지가 있고, 페이지 전체를 한 번에 제출하는 방식이라 바꾸지 않는 항목도 지금 값 그대로 같이 보내야 한다(`chunithm-arcade.md`와 같은 다섯 항목 — 상세는 그 문서 참고).

- 값은 CHUNITHM-NET 페이지에 실제로 있는 표시 텍스트 그대로 받는다(예: 스피드 "9.00", 가이드음 "5"). 모델이 숫자를 만들거나 단위를 변환하지 않는다 — `services/chunithm/arcade_settings.py`의 `build_update_payload`가 그 select의 `<option>` 표시 텍스트와 code로 정확히 맞춰 실제 SEGA `value`를 찾는다. 맞는 게 없으면 ValueError(사용자에게 그대로 보여줄 한글 메시지)로 거절한다.
- 바꾼 뒤 POST 응답을 믿지 않고 설정 페이지를 다시 읽어, 실제로 반영된 전체 값을 돌려준다(`update_arcade_settings`).
- 확인 버튼 없이 말하면 바로 반영한다(2026-10-20 사용자 결정 — 실제 인게임 데이터지만 언제든 다시 말로 되돌릴 수 있어 번거로움보다 즉시성을 택했다).
- 한 번에 여러 항목을 같이 바꿀 수 있다. 말 안 한 항목은 지금 값 그대로 둔다(건드리지 않는다).

## 즐겨찾기 곡

- `third_party`의 `get_favorite_music()`/`set_favorite_music()`를 그대로 쓴다(국제판 전용 어댑터에 이미 있다). `set_favorite_music()`은 전체 목록을 통째로 교체하는 방식이라, 추가·삭제 모두 지금 목록을 먼저 읽고 바꾼 전체 목록을 다시 써야 한다.
- 즐겨찾기 목록은 CHUNITHM-NET의 실제 musicId(숫자)만 돌려준다. 곡 이름으로 보여주려면 그 id를 우리 `songdb`로 되찾아야 하는데, 기존 `SongRecord`에는 id가 없었다(title로만 찾는 구조) — `songs.json` 시드의 각 엔트리에 있는 `id` 필드(SEGA 내부 musicId, Chunimaru 덧씌우기 응답의 `idx`와 같은 값)를 `SongRecord.id`로 가져오고, `SongDbService.by_id()`로 역방향 조회를 더했다.
- 같은 title이 여러 시드 엔트리로 merge될 때(WORLD'S END가 분리 엔트리인 경우 등)는 title·artist·aliases와 같은 규칙으로 처음 본 엔트리의 id를 대표 id로 쓴다. 즐겨찾기 추가는 이 대표 id로 한다 — WE 채보만 따로 즐겨찾기하는 조작은 다루지 않는다(실기 확인 전까지는 드문 경우로 본다).
- 추가·삭제는 곡 이름으로 받는다(`SongQuery` → `SongRecord`, 코어가 해석). 이미 있는 곡을 또 추가하거나 없는 곡을 빼려는 요청은 그 사실만 말하고 끝낸다(추가 네트워크 호출 없음).
- `songdb.by_id()`가 못 찾는 id(국제판 미수록이거나 데이터베이스가 안 따라간 곡)는 "모르는 곡"으로 표시한다.

## 도구

| 함수 | 하는 일 | 인자 | 슬래시 | display | confirm |
| --- | --- | --- | --- | --- | --- |
| `current_settings` | 지금 계정의 인게임 설정을 바로 읽어 보여줌(저장 안 함) | 없음 | `/현재설정` | optional | never |
| `change_settings` | 말한 항목만 바꾸고 전체 반영 결과를 보여줌 | 다섯 항목 전부 `str \| None = None` | `/게임설정 변경` | optional | never |
| `list_favorites` | 즐겨찾기 곡 목록(제목) | 없음 | `/즐겨찾기 목록` | required | never |
| `add_favorite` | 즐겨찾기에 곡 추가 | `song: SongQuery` | `/즐겨찾기 추가` | optional | never |
| `remove_favorite` | 즐겨찾기에서 곡 삭제 | `song: SongQuery` | `/즐겨찾기 삭제` | optional | never |

모든 도구가 `cost="network"`(CHUNITHM-NET을 직접 부른다), `mutates=True`는 `change_settings`·`add_favorite`·`remove_favorite`에만 — 셋 다 요청한 본인 계정만 바꾼다(`PlayerRef` 인자 없이 `ctx.player_id`로 고정).

## 이벤트

없음. 다른 스킬과 이벤트로 주고받지 않는다.

## 데이터

없음. 테이블을 두지 않는다 — 매번 CHUNITHM-NET을 그 자리에서 읽고 쓴다.

## 유니 지침 (SKILL.md 본문 초안)

- 설정 변경은 확인 없이 말하면 바로 반영하되, 결과에는 반영된 뒤 전체 설정값을 꼭 같이 보여준다.
- 바꿀 수 있는 값은 정해진 선택지뿐이다. 잘못된 값을 말하면 도구가 돌려준 오류를 그대로 전한다.
- 로그인이 안 돼 있거나 점검 중이거나 만료됐으면 그 사실만 말하고 끝낸다.

## 테스트

- 저장본 설정 페이지(`docs/reference/chunithm-net/arcade_settings.html`)로 `build_update_payload`가 바꾼 항목만 value를 바꾸고 나머지는 그대로 담는지, 모르는 표시값·모르는 항목이 ValueError로 걸리는지.
- `SongDbService.by_id()`가 로컬 시드 곡과 Chunimaru 전용 신곡 모두에서 id로 찾아지는지.
- 즐겨찾기 추가/삭제가 중복 추가·없는 곡 삭제를 조용히 거르는지(불필요한 네트워크 호출 없이).
- 로그인 안 됨·점검 중·로그인 만료 세 가지 실패를 각 도구가 구분해 말하는지.

## 구현 결정 (2026-10-20)

- `chunithm-arcade`의 `current_settings`(오락실에 안 묶인, 지금 계정을 바로 읽기만 하던 도구)를 이 스킬로 옮겼다. 표시 포맷 도우미(`FIELD_LABELS`, `named_from_settings`, `settings_line`)도 두 스킬이 같이 쓰도록 `services/chunithm/arcade_settings.py`로 옮겼다 — 스킬마다 복사하지 않는다(`docs/conventions.md` "공통 로직" 원칙).
- 설정 변경 POST(`/mobile/home/userOption/updateUserOption/update/`)는 CSRF 토큰이 아니라 `_t` 쿠키값(`adapter._token`, 기존에 다른 third_party 쓰기 메서드들도 쓰는 값)을 폼 필드 `token`으로 실어 보낸다. `ChunithmNetAdapterLike` Protocol에 `_token` 프로퍼티와 `get_favorite_music`/`set_favorite_music`을 추가해 서비스가 이 값에 접근한다.
- `SongRecord`에 `id: int`(SEGA 내부 musicId)를 새로 더했다 — 기존에 이 필드가 없는 테스트 픽스처(`chunithm-border`·`chunithm-sync`·`chunithm-goals`·`chunithm-report`·`songdb` 자체 테스트)는 자리표시용 id를 넣어 맞췄다. Chunimaru 전용(로컬 시드에 없는) 신곡은 그 응답의 `idx` 필드를 id로 쓴다.
