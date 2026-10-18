# chunithm-updates 저장본

2026-10-18에 실제로 받은 응답을 바탕으로 만든 샘플이다. 파서 시험은 이 파일들만 읽고, 실제
사이트에는 접속하지 않는다(`docs/conventions.md` §4).

| 파일 | 원본 | 줄인 방법 |
| --- | --- | --- |
| `jp_rss_sample.xml` | `https://info-chunithm.sega.jp/feed/`(`/feed`는 301로 이걸로 리다이렉트된다) | 최신 글 5개만 남기고 `<content:encoded>` 본문은 뺐다(파서가 안 쓴다) |
| `intl_news_sample.html` | `https://info-chunithm.sega.com/` | 전체가 13KB라 그대로 저장 |
| `jp_music_sample.json` | `https://chunithm.sega.jp/storage/json/music.json`(원본 1,759곡) | `newflag: "1"`(신곡) 3곡 + 일반곡 7곡, 총 10곡 |
| `intl_music_sample.json` | `https://chunithm.sega.com/assets/data/music.json`(원본 1,489곡) | 같은 방식으로 10곡 |

## 확인한 구조
- JP RSS: `<item>`마다 `<title>`(제목에 "M/D(요일)" 형식으로 업데이트 날짜가 들어 있다. 버전명은 title에 없다)·`<pubDate>`(업데이트보다 약 이틀 전)·`<link>`.
- 국제판 공지: `li.news--list__item` > `a.news--list__post` 안에 `div.news--date`(게시일, "YYYY.MM.DD")와 `p.news--title`. 제목이 `『버전명』Details of M/D Update` 형식이면 그 M/D가 업데이트 날짜, 『』 안이 버전명이다. 버전 자체를 알리는 글("Information of New 『...』 International Version")도 있다 — 이때도 『』로 버전명을 뽑을 수 있다.
- music.json: 리스트, 각 원소가 `id`(안정적인 고유 id)·`title`·`newflag`("1"이면 신곡)·`lev_bas`/`lev_adv`/`lev_exp`/`lev_mas`/`lev_ult`. 곡 목록 추가·삭제는 `id` 집합으로 비교한다(제목만으로는 리네임을 구분 못 한다).

**주의**: 다시 받아올 필요가 생기면 한 번만 호출하고 같은 방식으로 줄인다. 신곡 추가·삭제 비교(전/후) 시험은 이 샘플이 아니라 테스트 코드 안의 가상 데이터로 한다(두 시점 저장본이 없다).
