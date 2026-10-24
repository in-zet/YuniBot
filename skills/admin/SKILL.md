---
name: admin
description: 봇 관리. LLM 사용량과 비용, 서버별 스킬 켜기·끄기, 부여 태그, 스킬 등록 오류, maimai 곡 DB의 현재 버전을 다룬다.
summary: 봇 사용량, 서버별 스킬 설정, 태그 부여 같은 관리 일을 해. 관리자 전용이야.
examples:
  - 이번 달 LLM 비용 얼마야?
  - 이 서버에서 web-qa 꺼 줘
triggers: [비용, 사용량, 태그, 스킬 꺼, 스킬 켜, 스킬 등록, 곡 버전]
category: 관리
games: []
version: 1
services: [players, settings, registry_snapshot, usage, songdbs]
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
  - name: 보고 채널
    kind: str
    default: ""
    description: 서버 은어 자동 등록 같은, admin이 사후 검토할 일을 올리는 채널(id). 비우면 안 보낸다.
    scope: guild
---

## SKILL.md 본문

- 바꾸는 요청은 무엇이 바뀌는지 한 줄로 설명하고 확인 버튼을 낸다.
- 비용은 추정치임을 밝힌다.
