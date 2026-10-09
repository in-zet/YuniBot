---
name: practice
description: 2단계 코어 뼈대를 수동으로 확인하기 위한 연습용 스킬. 배포하지 않는다.
examples:
  - 연습 스킬 테스트
category: 메모
game: common
version: 1
services: []
defines_tags:
  - name: practice-unlocked
    description: 연습 체험을 시작한 플레이어
    how_to_get: "/연습 체험시작 실행"
    hidden: false
  - name: practice-secret
    description: 데모용 부여 태그 흉내(완전히 숨겨짐)
    how_to_get: 관리자가 FakeTagProvider에 직접 부여(데모 전용)
    hidden: true
---

이 스킬은 2단계 코어 통과 기준을 수동으로 확인하는 용도다. `/연습 체험시작`으로 상태 태그를 얻고,
`/연습 보기`는 그 태그가 있어야 쓸 수 있다. `/연습 비밀`은 완전히 숨겨진 부여 태그 데모다.
