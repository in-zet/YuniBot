---
name: account-link
description: 디코(디스코드) 계정 얘기일 때만 쓴다 — 디코 부계정을 한 플레이어로 연결하는 방법을 안내한다. 연결과 해제는 슬래시 명령으로만 한다. 그냥 "계정"은 세가 계정이다.
examples:
  - 디코 부계정도 연결하고 싶어
  - 연결된 계정 보여 줘
triggers: [부계정, 디코 계정, 디스코드 계정, 연결된 계정, 연결 해제, 연결해]
category: 계정 연결
game: common
version: 1
services: [players, notify, memory, db, player_data]
emits: [players.linked]
settings:
  - name: 알림을 받을 계정
    kind: str
    default: primary
    description: 동기화 알림을 어느 계정으로 멘션할지(primary/all/특정 디스코드 ID)
    scope: player
---

## SKILL.md 본문

- 연결 방법을 물으면 "`/연결 시작`을 대표로 쓸 계정에서, `/연결 참여`를 연결할 계정에서" 순서를 그대로 안내한다.
- "저 계정이 내 부계정이야, 연결해 줘"처럼 말로 요청해도 직접 연결하지 않는다. 양쪽 계정에서 명령을 써야 한다고 말한다.
