---
name: chunithm-account
description: 세가(오락실) 계정 = CHUNITHM-NET 계정의 로그인 상태 확인, 로그인 방법 안내, 기록 공개 설정을 다룬다. "디코"라고 안 붙인 "계정"은 이쪽이다.
examples:
  - 나 로그인 돼 있어?
  - 내 기록 다른 사람도 볼 수 있게 해 줘
triggers: [계정, 세가, 로그인, 로그아웃, 공개, 비공개, 내 기록 다른]
category: 계정 연결
game: chunithm
version: 1
weight: heavy
services: [chunithm_net, secrets, players, notify, records]
listens: [chunithm.login.expired]
defines_tags:
  - name: chunithm-linked
    description: CHUNITHM-NET 계정을 연결했다.
    how_to_get: "CHUNITHM-NET에 로그인하면 자동으로 얻는다. `/로그인`을 써 보라."
  - name: chunithm-synced
    description: CHUNITHM-NET 기록을 한 번 동기화했다.
    how_to_get: "CHUNITHM-NET 기록을 동기화하면 얻는다(동기화 기능은 아직 없다)."
---

- 로그인 요청에는 `/로그인`을 안내한다. SEGA ID나 비밀번호를 채팅으로 보내지 말라고 함께 말한다.
- 플레이어가 채팅에 ID/비밀번호나 쿠키처럼 보이는 값을 붙였으면, 그 값을 다시 말하지 말고 `/로그인`으로 다시 넣어 달라고 한다.
- OTP(2단계 인증)를 쓰는 계정은 지금 `/로그인`으로 못 들어간다고 안내한다.
- 공개 설정을 바꾸기 전에 무엇이 남에게 보이게 되는지 한 줄로 알려 준다.
