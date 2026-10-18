---
name: chunithm-session
description: 지난 플레이 세션(동기화 리포트)을 날짜나 오락실로 찾아 불러오고, 그 뒤 바뀐 점을 지금 값과 같이 본다.
examples:
  - 10월 세션 언제언제 했었지?
  - 가장 최근 겜플 세션 불러와 줘
  - 10월 5일 세션 보여줘
category: 세션과 후기
game: chunithm
version: 1
services: [records, arcades, players, db]
listens: [chunithm.sync.reported]
emits: [chunithm.session.viewed]
---

태그 조건은 없다(chunithm-report와 같다) — 공개된 남의 세션은 로그인 여부와 무관하게 볼 수 있다.

- 날짜 표현("지난달", "10월 초")은 지금 날짜 기준으로 실제 ISO 날짜(YYYY-MM-DD)나 start/end로 바꿔 넘긴다. 세션을 찾는 것 자체는 도구가 한다.
- `get_session`이 돌려주는 `rendered_text`는 그날 저장된 리포트 그대로다 — 다시 설명하거나 숫자를 바꿔 말하지 않는다.
- 덧붙일 말은 한두 문장만. 저장된 요약과 "그 뒤의 변화"(`changes`) 중 눈에 띄는 것 하나만 짚는다. 숫자는 도구 `data`의 값만 쓴다.
- 지금은 세션 후기(`review_open`)가 없다. 후기를 남기고 싶다고 하면 아직은 안 된다고 말한다.
- 세션이 없거나 그 오락실 기록이 없으면 도구가 돌려주는 제안(가장 가까운 날짜, 방문한 오락실 목록)을 그대로 안내한다.
