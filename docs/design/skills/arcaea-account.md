# 유니 스킬: arcaea-account

Nov 28, 2026 · @astrove

Arcaea(lowiro 계정)의 Arcaea Online 로그인을 플레이어에 연결하고 만료를 다루는 스킬이다(19단계 2단계, 사용자 결정 2026-11-28). SEGA 계정과 따로라 `sega-account`와 나눴다. 입력은 슬래시 명령의 입력창으로만 받고, 비밀번호와 쿠키는 어떤 경로로도 LLM에 닿지 않는다.

## 개요

```yaml
name: arcaea-account
games: [arcaea]
services: [chunithm_net, players, notify, records]   # chunithm_net 자리에 ArcaeaNetService가 낀다
listens: [arcaea.login.expired]
defines_tags: [arcaea-linked, arcaea-synced]
```

## 동작

- **로그인** `/아르케아 로그인`: 입력창에 아이디(또는 이메일)·비밀번호 칸과 쿠키 칸이 있고 둘 중 하나만 넣는다. 쿠키가 있으면 쿠키를 쓴다.
  - 아이디·비밀번호: 봇이 사이트 로그인 폼과 같은 요청(`POST /auth/login`)을 한 번 보내고 받은 `sid`만 저장한다. 아이디·비밀번호는 저장하지 않는다. 로그인 폼에 캡차는 없다(2026-11-28 사이트 코드 확인).
  - 쿠키: 구글·애플 가입 계정은 비밀번호가 없어서 브라우저에서 꺼낸 `Cookie:` 값을 통째로 붙인다. `sid`만 뽑아 프로필(`/webapi/user/me`)을 읽어 보고 되면 저장한다.
  - 성공하면 `arcaea_sessions`(코어 마이그레이션 032)에 암호화해 넣고 상태 태그 `arcaea-linked`를 붙인다. 실패 문구는 입력값을 다시 보여 주지 않는다.
- **상태** `status`, **로그아웃** `logout`(확인 버튼, 저장한 쿠키만 지운다 — 원격 로그아웃은 안 한다).
- **만료**: 동기화 중 401이면 `arcaea-linked`를 떼고 `arcaea.login.expired`를 내고, 이 스킬이 재로그인을 DM으로 한 번 안내한다.

## 구현 결정

- 쿠키 저장·상태 태그·만료 처리는 SEGA와 같은 틀(`services/common/account_cookies.py`의 `AccountCookieStore`)을 쓴다. 상태 태그 판별은 `-linked`·`-synced`로 끝나는 이름이다(`core/permissions.py`).
- 게임 프로필에 `account`(sega·arcaea)와 `login_command`를 두어, `sega-account`의 로그인·상태는 SEGA 게임만 보고 `sync`의 로그인 안내는 그 게임의 명령을 쓴다. `sync`는 `tags_any: [sega-linked, arcaea-linked]`이다. 기록 공개 설정(`sega-account`의 `set_privacy`)도 두 태그 중 하나면 쓸 수 있다.
- 아이디·비밀번호 로그인은 개발 중 실제 요청으로 확인하지 않았다(사용자 결정 — 배포 뒤 직접 써 본다). 쿠키 방식과 기록 읽기는 2026-11-28 실측했다.

## 미확인 사항

- `sid`가 언제 만료되는지 모른다.
- 아이디·비밀번호 로그인의 실제 응답(실패 때 상태 코드, csrf 헤더가 필요한지).
