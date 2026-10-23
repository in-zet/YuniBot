---
name: updates
description: 츄니즘 국제판·일본판 업데이트 일정과 신곡을 알려 주고, 업데이트 알림을 켜고 끈다.
summary: 국제판·일본판 업데이트 일정과 신곡을 알려 주고, 알림도 켜고 끌 수 있어.
examples:
  - 다음 업데이트 언제야?
  - 이번에 들어온 신곡 보여 줘
triggers: [업데이트, 알림, 일정, 신곡]
category: 알림
games: [chunithm]
version: 1
services: [songdb, records, notify, http, players, settings, scheduler, yuni, db]
emits: [chunithm.update.detected, updates.notice_due]
listens: [chunithm.sync.completed, updates.notice_due]
settings:
  - name: 알림 채널
    kind: str
    default: ""
    description: 업데이트 공지를 올릴 채널 id. 비우면 이 서버엔 알리지 않는다.
    scope: guild
  - name: 일본판 알림
    kind: bool
    default: true
    description: 일본판 업데이트도 이 서버에 알릴지. 국제판 알림은 늘 켜져 있다.
    scope: guild
  - name: 역할 멘션
    kind: str
    default: ""
    description: 업데이트를 알릴 때 같이 멘션할 역할(멘션 문자열). 비우면 멘션하지 않는다.
    scope: guild
---

# 유니 스킬: updates

- 업데이트 날짜와 신곡은 도구 결과로만 말한다. 모르는 날짜는 "아직 공지가 없다"고 말한다.
- 일본판 소식인지 국제판 소식인지 항상 밝힌다.
- 알림 요약은 두세 문장으로 한다.
- `/업데이트 설정`은 admin만 쓸 수 있다. 채널·역할은 각각 #채널 멘션/@역할 멘션으로 받고, id를 그대로 말해 주지 않는다.
