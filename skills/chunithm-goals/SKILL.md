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

스킬 자체의 태그 조건은 없다. 로그인하지 않아도 곡별 목표(`todo_*`)와 자유 목표(`set(kind="freeform")`)는
쓸 수 있다. 레이팅·오버파워·폴더 목표(`set`의 해당 kind, `todo_bulk`의 랭크/클리어/풀콤보/올저스티스
조건)는 `chunithm-synced`가 있어야 한다.

- 목표 종류가 애매하면 하나만 묻는다. 기한을 말하지 않으면 기한 없이 만든다.
- 달성률, 남은 양, 남은 어택은 도구 `data`의 값만 말한다. 남은 어택을 말할 때는 남은 점수도 함께 말한다.
- 미달을 탓하지 않는다. 반응 단계에 맞는 말과 제안만 한다.
- 일괄 추가(`todo_bulk`)는 대상 개수를 보여 주는 확인 버튼을 거친다.
- **레이팅 최종 목표는 플레이어당 하나뿐이다.** 이미 있는데 또 만들려고 하면 덮어쓰지 않고
  "이미 레이팅 목표가 있어. `/목표 수정`을 써 줘."라고 안내한다(설계서에 없던 세부, 여기서 정했다).
- 폴더 정의는 지금은 **상수 범위 + 난이도**만 지원한다(표기 레벨·수록 버전·장르는 다음에).
  `songdb` 서비스가 지금 공개하는 조회 방법이 상수 범위(`find_by_const`)뿐이라, 그 범위를 넘는
  폴더 정의는 아직 쿼리할 수단이 없다(구현 중 좁힌 범위).
- 오버파워 목표는 전체(overall)만 지원한다. 폴더별 오버파워 퍼센트는 이번 단계 범위 밖이다 —
  요청받으면 아직 안 된다고 분명히 말한다.
- `due_at`/`notify_offsets`는 데이터로만 저장한다. 기한 알림을 실제로 쏘는 예약은 다음 단계다.
