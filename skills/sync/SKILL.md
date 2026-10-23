---
name: sync
description: CHUNITHM-NET에서 최신 기록을 가져와 저장하고, 지난 동기화와의 차이를 리포트로 보여 준다.
summary: CHUNITHM-NET에서 최신 기록을 가져와서 지난번과 달라진 점을 알려 줄게.
examples:
  - 동기화해 줘
  - 오늘 친 거 갱신해서 크로노만 봐 줘
triggers: [동기화, 싱크, 갱신]
category: 기록과 리포트
game: chunithm
version: 1
weight: heavy
services: [chunithm_net, records, settings, yuni, notify, players, tables]
tags: [chunithm-linked]
emits: [chunithm.sync.completed, chunithm.sync.reported]
settings:
  - name: 동기화 쿨다운
    kind: int
    default: 120
    description: /동기화 재실행을 막는 최소 간격(초)
    scope: guild
---

- 동기화 요청에 특정 채보만 보라는 지시가 붙으면 `focus`로 넘긴다(지금은 채보 하나만 지정할 수 있다). 지시가 없으면 인자 없이 부른다.
- 어느 오락실에서 쳤는지 말했으면 `arcade`로 넘긴다.
- 리포트는 표시물로 나가고 유니의 한마디도 이미 들어 있다. 멘션 답에서는 리포트 내용을 다시 설명하지 않는다.
- 리포트를 DM으로 보냈다는 결과면 DM을 확인하라고만 말한다.
- 동기화가 실패하면 원인과 해결 방법을 정확히 말한다. 로그인이 풀렸으면 `/로그인`을 안내한다. 쿨다운이면 남은 시간(결과의 숫자 그대로)을 말한다.
