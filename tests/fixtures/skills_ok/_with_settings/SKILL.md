---
name: with-settings
description: 설정 두 개(플레이어/서버 범위)를 선언하는 픽스처.
examples:
  - 설정 테스트
category: 메모
games: []
version: 1
services: []
settings:
  - name: 알림
    kind: bool
    default: true
    description: 동기화 때 덧붙이는 말
    scope: player
  - name: 기준일
    kind: int
    default: 14
    description: 오랜만의 기준
    scope: guild
---

설정 화면 계약 테스트용 픽스처.
