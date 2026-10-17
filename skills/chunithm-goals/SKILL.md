---
name: chunithm-goals
description: 레이팅·오버파워·폴더 순회·곡별(To-Do)·자유 목표를 기한과 함께 정하고, 진행률을 보고, 기한 알림을 받는다.
examples:
  - 12월 31일까지 17.00 목표로 잡아 줘
  - 15.0에서 15.3 마스 중 SSS 안 된 거 전부 투두에 넣어 줘
  - 내 목표 어디까지 왔어?
category: 목표
game: chunithm
version: 1
services: [records, songdb, notify, players, yuni, db]
listens: [chunithm.sync.completed, chunithm.song.viewed, chunithm.plan.requested]
emits: [chunithm.goal.completed]
---

스킬 자체의 태그 조건은 없다. 로그인하지 않아도 곡별 목표(`todo_*`)와 자유 목표
(`set_goal(kind="freeform")`)는 쓸 수 있다. 레이팅·오버파워·폴더 목표(`set_goal`의 해당 kind,
`todo_bulk`의 랭크/클리어/풀콤보/올저스티스 조건)는 `chunithm-synced`가 있어야 한다.

- 목표 종류가 애매하면 하나만 묻는다. 기한을 말하지 않으면 기한 없이 만든다.
- 달성률, 남은 양, 남은 어택은 도구 `data`의 값만 말한다. 남은 어택을 말할 때는 남은 점수도 함께 말한다.
- 미달을 탓하지 않는다. 반응 단계에 맞는 말과 제안만 한다.
- 일괄 추가(`todo_bulk`)와 여러 개 삭제(`delete`)는 대상이 2개 이상이면 개수를 보여 주는 확인
  버튼을 거친다(0~1개면 바로 실행된다 — 실행기의 `confirm="bulk"`).
- 곡·난이도·랭크·점수는 플레이어가 말한 그대로 넘긴다(`chart`, `difficulty`, `rank`,
  `target_score`). 해석기가 정식 곡 제목·난이도 코드·기준 점수로 바꾼다 — 랭크를 점수로
  직접 환산하거나 곡 이름을 고쳐 쓰지 않는다.
- **레이팅 최종 목표는 플레이어당 하나뿐이다.** 이미 있는데 또 만들려고 하면 덮어쓰지 않고
  "이미 레이팅 목표가 있어. `/목표 수정`을 써 줘."라고 안내한다(설계서에 없던 세부, 여기서 정했다).
- **곡별 목표(`todo_add`/`set_goal(kind="song")`)는 이미 그 점수를 넘긴 채보면 만들지 않는다.**
  "이미 그 목표를 달성했어."라고 바로 알린다 — `todo_bulk`가 이미 조건을 채운 채보를 조용히
  빼는 것과 달리, 한 건짜리 도구는 플레이어가 고른 그 채보 하나를 콕 집어 물은 것이므로
  침묵보다 분명한 에러가 맞다고 판단했다.
- 폴더 정의는 **상수 범위, 표기 레벨, 수록 버전 중 하나 + 난이도**를 지원한다(장르는 songdb에
  그 필드가 없어 이번 범위 밖). `set_goal(kind="folder")`는 `folder_min_const`+`folder_max_const`,
  `folder_level`, `folder_version` 중 정확히 하나와 `folder_difficulty`를 받는다. 버전으로 "그
  버전의 모든 난이도"를 한 번에 묶는 건 songdb 서비스 자체(`find_by_version(difficulty=None)`)는
  할 수 있지만, 이 스킬의 폴더 목표는 설계서("폴더 정의는 ~ 중 하나에 난이도를 조합한다")를 따라
  난이도를 항상 요구한다 — 쓰지 않는다.
- 오버파워 목표는 전체(overall)만 지원한다. 폴더별 오버파워 퍼센트는 이번 단계 범위 밖이다 —
  요청받으면 아직 안 된다고 분명히 말한다.
- `due_at`/`notify_offsets`는 데이터로만 저장한다. 기한 알림을 실제로 쏘는 예약은 다음 단계다.
  `due_at`은 YYYY-MM-DD 날짜만 받고, 꼬리표를 안 주면 기한으로 단기/중기/장기를 정한다.
- 레이팅·오버파워 목표는 지금 값보다 높아야 만들 수 있다(이미 달성했거나 내려가야 닿는 목표는 거절).
- 남의 목표는 목표마다 고른 공개 여부(`public`)로 거른다. 다만 공개 목표라도 그 사람이 기록을
  비공개(`records_public` 꺼짐)로 해 두었으면 목표 정의만 보이고 현재값·달성률·남은 양은 빠진다
  ("진행 상황은 비공개야"). 그 숫자를 짐작해 말하지 않는다.
- 곡별 목표는 점수(`target_score`)나 램프(`lamp`: 풀콤보 `full_combo`, 올 저스티스 `all_justice`)
  중 하나다. 풀콤보·올 저스티스는 점수로 바꾸지 않고 램프로 판정한다.
- `list_goals`/`todo_list`/`progress`는 목표 id를 함께 보여 준다. `update`/`progress`/`todo_done`/
  `delete`에는 그 id를 넘긴다.
