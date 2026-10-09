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
