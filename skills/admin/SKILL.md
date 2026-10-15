---
name: admin
description: 봇 관리. LLM 사용량과 비용, 서버별 스킬 켜기·끄기, 부여 태그, 스킬 등록 오류를 다룬다.
examples:
  - 이번 달 LLM 비용 얼마야?
  - 이 서버에서 web-qa 꺼 줘
category: 관리
game: common
version: 1
services: [players, settings, registry_snapshot, usage]
tags: [admin]
settings:
  - name: 월 비용 상한
    kind: float
    default: 10.0
    description: 이 서버의 월 LLM 비용 상한(달러 추정)
    scope: guild
  - name: 플레이어 쿨다운
    kind: int
    default: 10
    description: 플레이어별 도구 호출 쿨다운(초)
    scope: guild
---

## SKILL.md 본문

- 바꾸는 요청은 무엇이 바뀌는지 한 줄로 설명하고 확인 버튼을 낸다.
- 비용은 추정치임을 밝힌다.
