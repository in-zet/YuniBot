---
name: chunithm-arcade
description: 오락실별로 츄니즘 게임 설정(스피드, 필드 월, 판정 타이밍 A·B, 가이드음)을 저장하고 꺼내 보여 준다.
examples:
  - 겜플 세팅 알려 줘
  - 게임플라자 설정 바뀐 거 보여 줘
triggers: [오락실, 겜플, 게임플라자, 방문, 세팅]
category: 오락실
game: chunithm
version: 1
services: [chunithm_net, arcades, records, db]
listens: [chunithm.sync.completed, chunithm.sync.reported, chunithm.session.viewed]
---

# 유니 스킬: chunithm-arcade

오락실 목록·별칭·방문 기록은 `arcades` 공통 서비스가 갖고 있다. 이 스킬은 오락실마다의 츄니즘 게임 설정만 다룬다.

- 오락실 선택은 버튼이 아니라 말로 받는다(동기화 리포트는 텍스트만 실어 보낸다). `/동기화`에 오락실을 같이 말하거나, 나중에 `/오락실 방문`으로 알려준다.
- 설정을 물으면 지금 계정 설정(가장 최근에 읽은 값)과 다른 항목부터 말한다. 다른 항목이 없으면 "그대로 쓰면 된다"고 말한다. 마지막 플레이 날짜를 함께 말한다.
- 오락실을 말하지 않고 "지금 내 인게임 설정"을 물으면 `chunithm-settings` 스킬의 `current_settings`로 보낸다 — 이 스킬은 오락실별 방문 이력에 묶인 조회만 한다.
- 오락실 이름이 확실하지 않으면 추측해서 저장하지 않는다 — `ArcadeRef`가 돌려주는 확인(코어의 범용 확인 버튼)으로 확정받은 뒤에만 별칭을 저장한다.
- 지금은 세션 후기나 설정 변경(디스코드에서 값을 바꿔 달라는 요청)은 다루지 않는다. 설정 변경은 `chunithm-settings` 스킬의 일이다. 이 스킬은 오락실 방문 이력에 묶어 읽어서 보여주기만 한다.
