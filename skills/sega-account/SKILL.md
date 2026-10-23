---
name: sega-account
description: 세가(오락실) 계정 = CHUNITHM-NET 계정의 로그인 상태 확인, 로그인 방법 안내, 기록 공개 설정을 다룬다. "디코"라고 안 붙인 "계정"은 이쪽이다.
summary: CHUNITHM-NET 로그인과 기록 공개 설정을 맡고 있어.
examples:
  - 나 로그인 돼 있어?
  - 내 기록 다른 사람도 볼 수 있게 해 줘
triggers: [계정, 세가, 로그인, 로그아웃, 공개, 비공개, 내 기록 다른]
category: 계정 연결
games: [chunithm]
version: 1
weight: heavy
services: [chunithm_net, secrets, players, notify, records]
listens: [sega.login.expired]
defines_tags:
  - name: sega-linked
    description: CHUNITHM-NET 계정을 연결했다.
    how_to_get: "`/로그인`으로 CHUNITHM-NET 계정을 연결하면 동기화와 인게임 설정을 쓸 수 있어."
  - name: chunithm-synced
    description: CHUNITHM-NET 기록을 한 번 동기화했다.
    how_to_get: "로그인한 뒤 `/동기화`로 기록을 한 번 가져오면 레이팅, 베스트, 세션 같은 기록 기능을 쓸 수 있어."
---

- 로그인 요청에는 `/로그인`을 안내한다. SEGA ID나 비밀번호를 채팅으로 보내지 말라고 함께 말한다.
- 플레이어가 채팅에 ID/비밀번호나 쿠키처럼 보이는 값을 붙였으면, 그 값을 다시 말하지 말고 `/로그인`으로 다시 넣어 달라고 한다.
- OTP(2단계 인증)를 쓰는 계정은 지금 `/로그인`으로 못 들어간다고 안내한다.
- 공개 설정을 바꾸기 전에 무엇이 남에게 보이게 되는지 한 줄로 알려 준다.
- 로그인이 안전한지 물으면: ID/비밀번호는 저장하지 않고, 로그인에 성공해서 받은 세션 쿠키(clal)만 암호화해 보관하며 `/로그아웃`하면 지운다고 답한다. `/로그인` 창에도 같은 안내가 있다.
