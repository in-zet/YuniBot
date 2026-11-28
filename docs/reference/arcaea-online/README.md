# Arcaea Online 응답 표본

`https://webapi.lowiro.com`의 실제 응답을 2026-11-28 사용자 계정(Arcaea Online 구독 중)으로 GET 한 번씩 받아, 개인 정보 칸(`user_id`·`name`·`display_name`·`user_code`·`email`)을 지운 것이다.

| 파일 | 경로 | 내용 |
| --- | --- | --- |
| `score_rating_me.json` | `/webapi/score/rating/me` | `best_rated_scores` 50개: `song_id`·`difficulty`(0 PST … 4 BYD)·점수·판정 수·`clear_type`·곡별 포텐셜 `rating`(소수 끝까지)·제목(`en`/`ja`) |
| `score_song_me_all_ftr_p1.json` | `/webapi/score/song/me/all?difficulty=2&page=1&sort=date&term=` | FTR 기록 첫 페이지(10개, `count` 199). 위 칸에 더해 `shiny_perfect_count`·`score_below_max`·`best_clear_type`·`health`·`time_played`(ms)·`yearly_play_count` |

## 실측으로 안 것

- 사이트(`arcaea.lowiro.com`)는 SPA이고 API 기준 주소는 `//webapi.lowiro.com`, 쿠키 인증(`withCredentials`), 쓰기 요청은 `csrf` 쿠키를 `X-CSRF-TOKEN` 헤더로 보낸다. 로그인 쿠키는 `sid`다(그 밖엔 `ctrcode`·구글 분석).
- 로그인 폼은 아이디(또는 이메일)·비밀번호만 받고(`POST /auth/login`) 캡차는 가입(`/authapi/account`)에만 있다. 구글·애플·페이스북 로그인(`/authapi/auth/login/oauth/…`)도 있다.
- `/webapi/user/me`: 프로필. `rating`은 포텐셜 ×1000 정수(표본 계정 11366 = 11.366), `recent_score`는 마지막 1판뿐, 구독 만료 시각 `arcaea_online_expire_ts`. 이메일 등 개인 정보가 많아 표본으로 남기지 않았다.
- 전체 기록은 난이도별 10개씩 페이지로 오고 `sort=date`면 최근 순이다.
- `clear_type`: 1은 Lost가 있는 클리어, 2는 Lost 0·Far 있음(FULL RECALL), 3은 Far·Lost 0(PURE MEMORY), 4는 표본에 한 번(9,911,486점) — 0·4·5의 뜻은 아직 대조 못 했다.
- 노트 수 = Pure + Far + Lost.
- **포텐셜 공식(7.0.0, 2026-08-27 개정 — [팬 위키](https://arcaea.miraheze.org/wiki/Gameplay_and_scoring))**: 곡별 = 상수 + 점수 보정(10,000,000↑ +2, 9,800,000↑ +1+(점수−9.8M)/200k, 그 아래 (점수−9.5M)/300k) + 클리어 보너스 0.2(TRACK LOST가 아니면). 전체 = (베스트 상위 10개 ×2 + 나머지 40개) ÷ 60, 셋째 자리에서 버림. 최근 기록은 더 이상 안 들어간다. 표본 50개로 계산하면 11.36693… → 11.366으로 프로필 값과 같다.
- 곡별 `rating`에서 위 공식으로 거꾸로 낸 상수는 비공식 상수 자료(DoriTran 2026-08, OllyDoge 2026-02)와 거의 다 맞는다(처음엔 클리어 보너스를 몰라 0.2씩 어긋나 보였다).
