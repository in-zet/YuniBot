---
name: arcaea-account
description: Arcaea(lowiro 계정, Arcaea Online) 로그인 상태 확인, 로그인 방법 안내, 로그아웃을 다룬다. 세가 계정과 따로다.
summary: Arcaea Online 로그인을 맡고 있어.
examples:
  - 아르케아 로그인 돼 있어?
  - 아르케아 로그아웃해 줘
triggers: [로그인 아르케아, 아르케아 로그인, 아르케아 계정, 아르케아 로그아웃, arcaea online, 아르케아 온라인]
category: 계정 연결
games: [arcaea]
version: 1
weight: light
services: [chunithm_net, players, notify, records]
listens: [arcaea.login.expired]
defines_tags:
  - name: arcaea-linked
    description: Arcaea Online 계정을 연결했다.
    how_to_get: "`/로그인 아르케아`으로 Arcaea 계정을 연결하면 Arcaea 기록을 동기화할 수 있어(Arcaea Online 구독이 필요해)."
  - name: arcaea-synced
    description: Arcaea 기록을 한 번 동기화했다.
    how_to_get: "로그인한 뒤 `/동기화 game:아르케아`로 기록을 한 번 가져오면 포텐셜, 베스트 같은 기록 기능을 쓸 수 있어."
---

- 로그인 요청에는 `/로그인 아르케아`을 안내한다. 창에 계정 ID(또는 이메일)·비밀번호를 넣거나, 구글·애플로 가입해 비밀번호가 없으면 브라우저에서 꺼낸 쿠키를 넣는다. 채팅으로 비밀번호나 쿠키를 보내지 말라고 함께 말한다.
- 플레이어가 채팅에 비밀번호나 쿠키처럼 보이는 값을 붙였으면, 그 값을 다시 말하지 말고 `/로그인 아르케아`으로 다시 넣어 달라고 한다.
- 기록은 Arcaea Online 구독자만 가져올 수 있다고 알려 준다.
- 쿠키 꺼내는 법을 물으면: PC 브라우저에서 arcaea.lowiro.com에 로그인 → 프로필의 Scores 화면 → F12 → Network → 새로고침 → `webapi`로 거른 요청 하나 → Request Headers의 `Cookie:` 값을 통째로 복사해 창에 붙인다.
- 로그아웃(`logout`)은 "아르케아 로그아웃"처럼 분명히 말했을 때만 부른다. 그냥 "로그아웃"이면 세가 로그아웃인지 아르케아 로그아웃인지 묻는다.
- 로그인이 안전한지 물으면: ID·비밀번호는 저장하지 않고, 로그인으로 받은 세션 쿠키(sid)만 암호화해 보관하며 로그아웃하면 지운다고 답한다.
