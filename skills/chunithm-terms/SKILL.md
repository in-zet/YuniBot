---
name: chunithm-terms
description: 츄니즘 용어와 은어(트플플, 이론치, 오버파워, 비비기 등)의 뜻을 설명하고, 서버에서 쓰는 은어를 등록한다.
examples:
  - 오버파워가 뭐야?
  - 트플플가 몇 점부터야?
  - 비비기가 뭐야?
category: 질문 답변
game: chunithm
version: 1
services: [terms, players, notify]
---

- 용어 뜻은 사전에 있는 내용만 말한다. 사전에 없으면 `web-qa`를 펼친다.
- 랭크·램프의 점수 기준은 도구 결과의 값만 쓴다.
- 서버 은어를 설명할 때는 이 서버에서 쓰는 말이라고 밝힌다.
- 플레이어가 모르는 은어를 써서 해석에 실패하면, 뜻을 물어보고 용어 제안을 권할 수 있다.
