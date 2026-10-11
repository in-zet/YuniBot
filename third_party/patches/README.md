# third_party 패치

`third_party/` 파일 자체는 손으로 고치지 않는다(CLAUDE.md). 원본과 다르게
동작해야 할 부분이 있으면 여기에 `.patch` 파일로 남기고, `scripts/sync_upstream.py`가
업스트림을 복사한 뒤 자동으로 적용한다. 패치가 더 이상 깔끔히 적용되지 않으면
`sync_upstream.py`가 실패하고, 그때 사람이 새 업스트림 버전에 맞춰 다시 diff를 뜬다.

## 0001-chunithm-net-adapter-no-local-db.patch

`ChunithmNetAdapter`의 점수 조회 메서드는 원래 chuni-penguin 자신의
SQLAlchemy DB에 묶여 있다(곡 메타데이터 보강, 레이팅 계산, PB upsert, 숨김곡
보정). 우리는 그 DB를 쓰지 않고 `songdb`/`records` 서비스가 같은 일을 하므로,
이 패치는 그 4곳(`_process_records`, `_get_hidden_personal_bests`,
`get_rating_breakdown`의 숨김곡 보정, ONGEKI 레이팅 분기)을 "DB 없이 원본
레코드를 그대로 반환"으로 바꾼다. 이 패치 덕분에 `chuni_penguin.database`,
`chuni_penguin.logging`, `chuni_penguin.config`, SQLAlchemy, structlog가
전부 필요 없어진다.

패치 적용 후 `self.database`를 읽는 코드는 생성자(`chuni_penguin/adapters/base.py`)
말고는 남지 않는다. 그래서 우리 쪽에서 `ChunithmNetAdapter(database=None, ...)`로
생성해도 된다 — `database`는 저장만 되고 아무도 읽지 않는다.

**이 패치가 가져오는 부작용**: DB 보강을 안 하므로, 이 어댑터가 돌려주는 레코드의
`rating`, `chart.internal_level` 같은 계산값은 전부 `None`이다. 특히
`get_rating_breakdown(RatingType.naive)`는 이 값들로 정렬·합산을 하는데, 에러 없이
레이팅 0을 조용히 돌려준다(정렬도 임의 순서가 된다). `records` 서비스가 실제
계산을 하기 전까지, 이 어댑터의 레이팅 관련 반환값(naive 레이팅, `rating`,
`internal_level` 필드)은 믿지 않는다. 4단계에서 `records`가 이 계산을 대신한다.

## 0002-chunithm-net-adapter-public-httpx-aiohttp-name.patch

업스트림은 자기 저장소 안의 `packages/httpx-aiohttp/`(로컬 포크, 버전 `0.1.0`)를
쓰는데, 그 포크의 전송 클래스 이름이 `AIOHTTPTransport`다. PyPI에 공개된
`httpx-aiohttp`(우리가 쓰는 것, `0.2.0`으로 고정)는 같은 클래스를 `AiohttpTransport`로
부른다 — 버전이 아니라 이름 자체가 다른 포크라 어느 PyPI 버전을 깔아도 못 맞춘다.
이 패치는 그 참조 하나만 PyPI 쪽 이름으로 바꾼다.

**생성자 시그니처는 맞지만 동작은 다르다**: `retries` 키워드는 두 쪽 다 받는다.
업스트림의 포크는 이걸 실제로 쓴다 — `packages/httpx-aiohttp/src/httpx_aiohttp/_transport.py`의
`RetryMiddleware`가 연결 실패를 지수 백오프로 재시도한다. PyPI의 `0.2.0`은
`retries` 값을 저장만 하고 아무 데도 안 쓴다 — 즉 지금은 `retries=5`를 줘도
실질적으로 `retries=0`이다. 미니 PC가 매일 새벽 꺼져 있다가 다시 켜질 때처럼
일시적인 연결 실패가 날 상황에서, 지금은 업스트림과 달리 재시도가 없다.

업스트림의 포크(`packages/httpx-aiohttp/`)도 같은 저장소의 0BSD 라이선스 아래
있어서 그대로 들여올 수는 있다. 그 작업과 재시도 정책을 우리가 직접 어떻게
가져갈지는 4단계(`chunithm_net` 서비스, 접속 횟수 제한을 다루는 곳)에서 다시
정한다 — 서비스 자체가 재시도/속도 제한 정책을 가지므로, 포크를 통째로 들여오는 것과
서비스 레벨에서 재시도를 새로 짜는 것 중에 그때 고른다.

## 0003-chunithm-net-rating-detail-missing-idx.patch

`parse_music_for_rating`(`get_best30()`/`get_new20()`이 쓰는 파서)은 각 곡 폼 안의
`<input name="idx">` 숨은 필드에서 `song.id`를 읽는다. 그런데 국제판
`ratingDetailBest`/`ratingDetailRecent` 페이지의 실제 마크업에는(2026-10-11 저장본
기준) 이 필드가 없다 — 패치 전에는 `AttributeError`로 죽는다. 이 패치는 필드가
없으면 `song.id = 0`으로 둔다. 우리 쪽 `songdb`/`records` 서비스는 `song.id`를
전혀 안 쓰고 제목+난이도로만 매칭하므로 안전하다(단, vendored
`get_rating_breakdown`처럼 `song.id`로 중복을 거르는 메서드는 이 두 페이지를 통해
호출하면 여러 곡이 `id=0`으로 뭉쳐 보일 수 있다 — 우리는 `get_best30()`/
`get_new20()`만 직접 쓰고 `get_rating_breakdown()`은 쓰지 않는다).
