# maimai DX NET 페이지 표본

`https://maimaidx-eng.com/maimai-mobile/` 의 실제 응답을 2026-10-30에 한 번씩 받아 개인 정보를 지우고(닉네임·칭호·프로필/캐릭터 그림 id·플레이 상세용 `idx` 토큰) 목록을 줄인 것이다. 파서 시험(`tests/services/maimai/test_parser.py`)이 읽는다.

| 파일 | 경로 | 내용 |
| --- | --- | --- |
| `rating_target.html` | `home/ratingTargetMusic/` | 프로필 블록 + "Songs for Rating(New)" 4곡 + "(Others)" 3곡. 실제 페이지에는 비어 있는 "Songs for Rating Selection(New/Others)" 머리글이 뒤에 더 있다 |
| `recent.html` | `record/` | 최근 플레이 6개(DX 4 + STD 2). 이 계정에는 FC/AP/싱크 기록이 없어 램프 그림은 `fc_dummy`·`sync_dummy`뿐이다 |
| `music_master.html` | `record/musicSort/search/?search=V&sort=1&playCheck=on&diff=3` | 마스터 난이도 기록 7곡, 버전별 머리글 포함 |
| `music_remaster.html` | 같은 경로 `diff=4` | Re:MASTER 1곡 |
| `music_utage.html` | `search=V&sort=1&diff=10`(`playCheck` 없음) | 宴 3곡, 친 적 없는 곡이라 점수 칸이 없다. 난이도 칸 클래스 `music__score_back`(이름이 비어 있음) |
| `music_empty.html` | 같은 경로 `diff=0` | 친 곡이 없는 난이도 |
| `error_session.html` | `error/` | 로그인이 풀렸을 때 가는 오류 페이지(`ERROR CODE：100001`) |

## 실측으로 안 것

- `site_id=maimaidxex`, 돌아올 주소 `https://maimaidx-eng.com/maimai-mobile/`. CHUNITHM에서 받은 `clal`로 그대로 로그인된다.
- 로그인이 풀린 채로 페이지를 부르면 `/maimai-mobile/error/`(코드 100001)로 보낸다. 루트 `/maimai-mobile/`은 게이트웨이 로그인으로 보낸다.
- 베스트 목록은 `home/ratingTargetMusic/`가 준다: 신곡(New) 15 + 구곡(Others) 35. 곡 레벨(상수 아님)·달성률·랭크 그림이 있고 램프·DX 점수는 없다.
- 이 목록과 곡 DB(otoge-db) 상수로 계산한 레이팅이 프로필의 레이팅과 정확히 같았다(8564).
- 난이도별 전체 기록은 `record/musicSort/search/?search=V&sort=1&playCheck=on&diff=N`(N = BAS 0 … REM 4, 宴 10)이다. 한 요청에 한 난이도의 친 곡 전부가 달성률순으로 온다.
- 목록 항목의 오른쪽 그림 3개는 HTML 순서로 싱크·콤보·랭크다. 램프가 없으면 `music_icon_back.png`가 자리를 채운다.
- 채보 종류는 `music_dx.png`/`music_standard.png` 그림으로 갈린다. 宴은 모두 `music_standard.png`다.
- 최근 플레이는 최대 50개이고 시각은 JST다.
