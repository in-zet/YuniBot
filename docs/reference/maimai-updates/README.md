# maimai-updates 저장본

2026-11-27에 실제로 받은 공개 페이지를 줄인 것이다(로그인 없음, 개인 정보 없음). 파서 시험만 읽는다.

| 파일 | 원본 | 줄인 방법 |
| --- | --- | --- |
| `intl_top_sample.html` | `https://maimai.sega.com/` | 첫 화면의 "Latest Updates" 목록(`ul.top--update__list`)만, 항목 15개 중 최신 4개 |
| `jp_rss_sample.xml` | `https://info-maimai.sega.jp/feed/` | 최신 글 4개, `<content:encoded>` 본문은 뺐다 |

## 확인한 구조
- 국제판에는 글 공지가 없다(`info-maimai.sega.com` 없음). 공식 사이트 첫 화면의 공지 슬라이드는 매장 홍보 그림(`assets/data/banners.json` → `/download/<날짜>-<n>/pop`)뿐이라 글로 읽을 수 있는 건 출시 목록 하나다: `li.top--update__item` > `p.top--update__time`("2026.7.23")·`p.top--update__text`("maimai DX CiRCLE PLUS International Version is launched!(…)").
- 일본판 RSS는 츄니즘 일본판과 같은 WordPress 구조이고 제목이 "M/D(요일)…"로 시작하면 그 날짜가 업데이트 날짜다. 이벤트·점검 안내 글도 섞여 있다.
- 곡 목록: 국제판 `https://maimai.sega.com/assets/data/maimai_songs.json`(1,467곡), 일본판 `https://maimai.sega.jp/data/maimai_songs.json`(1,617곡). `id`가 없고 `sort`가 목록 안에서 겹치지 않는다(같은 제목은 국제판에 하나 있다). `release`(YYMMDD)·`date`("NEW")도 있지만 비교는 `sort` 집합으로 한다.
