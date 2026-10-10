---
name: account-link
description: 디스코드 부계정을 한 플레이어로 연결하는 방법을 안내한다. 연결과 해제는 슬래시 명령으로만 한다.
examples:
  - 디코 부계정도 연결하고 싶어
  - 연결된 계정 보여 줘
category: 계정 연결
game: common
version: 1
services: [players, notify]
emits: [players.linked]
---

## SKILL.md 본문

- 연결 방법을 물으면 "`/연결 시작`을 대표로 쓸 계정에서, `/연결 참여`를 연결할 계정에서" 순서를 그대로 안내한다.
- "저 계정이 내 부계정이야, 연결해 줘"처럼 말로 요청해도 직접 연결하지 않는다. 양쪽 계정에서 명령을 써야 한다고 말한다.
