---
name: chunithm-report
description: 내 레이팅, 베스트 30과 신곡 20, 최근 플레이, 곡별 기록을 보여 주고 레이팅을 올릴 후보곡을 분석한다. 공개한 다른 플레이어의 기록도 볼 수 있다.
examples:
  - 내 베스트 30 보여 줘
  - 레이팅 올릴 곡 추천해 줘
  - 민수 크로노 기록 몇 점이야?
  - 내 레이팅 얼마야?
  - 최근에 친 거 판정까지 보여 줘
category: 기록과 리포트
game: chunithm
version: 1
services: [records, songdb, players, chunithm_net]
tags: [chunithm-synced]
listens: [chunithm.sync.completed]
---

- 레이팅과 평균은 현재값과 변화량을 함께 말한다. 변화량만 따로 말하지 않는다.
- 레이팅 하나만 물으면 말로 답하고, 목록과 분석은 표시물로 보여 준다.
- 나쁜 결과나 부족한 점수는 돌려 말하지 않고 숫자 그대로 전한다.
- 다른 플레이어의 기록이 비공개라는 오류가 오면 비공개라고만 말한다. 다른 경로로 찾지 않는다.
- 누구 기록인지 말하지 않았으면 `player`를 비워 둔다(본인 기록을 본다).
- `recent`는 CHUNITHM-NET에 직접 접속한다. 판정 수는 앞 20개에만 붙는다.
- 동기화 리포트 반응 단계는 지금 축소판이다: SSS 이상 갱신이 있으면 great, 그 밖의 갱신이 있으면 good, 갱신 없이 플레이만 했으면 close, 변화 없음은 단계 없이 간단한 조각만 낸다(rest와 "평소 점수 수준" 기준은 실제 기록을 보고 다음에 정한다).
