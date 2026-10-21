---
name: my-skill
description: 무엇을 하고 언제 쓰는지 200자 이내로.
summary: 플레이어에게 보여 줄 한두 문장. 유니 말투로("~해 줄게").
examples:
  - 이 스킬을 골라야 하는 질문 예시 1
  - 질문 예시 2
triggers: [메시지에 있으면 이 스킬을 미리 펼칠 낱말]
category: 메모
game: common
version: 1
services: []
---

# 유니 스킬: _template

새 스킬은 이 폴더를 복사해서 만든다. `_`로 시작하는 폴더는 레지스트리가 스캔하지 않는다.
복사한 뒤 할 일:

- 위 `---` 사이의 manifest(앞부분 YAML)를 고친다. `name`은 소문자, 숫자, 하이픈만 쓴다.
- `skill.py`의 예시 도구를 바꾼다.
- `tests/test_template_example.py`를 `tests/test_<스킬>.py`로 이름을 바꾸고 이 스킬의 테스트를 쓴다
  (pytest는 같은 파일 이름이 두 번 나오면 수집에 실패한다).
- 테이블이 필요하면 `migrations/001_init.sql`을 만든다. 테이블 이름은 스킬 이름 접두사로 시작한다.

## SKILL.md 본문

이 줄 아래가 펼칠 때만 모델에 들어가는 지침이다. 어느 도구를 언제 쓰는지, 결과를 어떻게 말할지를 쓴다.
