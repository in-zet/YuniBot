# Chunimaru songdb 저장본

`songdb_sample.json`은 `https://chunimaru.fuwamofu.net/api/songdb/intl`을 2026-10-11에 1회 호출해 받은 실제 응답을 바탕으로 만들었다(ETag `"be6df47f114e0e00f02cc81929b39fd493c1cbc6"`, `generatedAt`/`newSongCutoff`/`songCount`/`chartCount` 등 상위 메타데이터는 원본 그대로 두었다). 전체 응답은 1,574곡(172KB, gzip 전송)이라 그대로 커밋하지 않고, `songs` 배열만 아래 사례를 포함하도록 11곡으로 줄였다:

| 곡 | 포함한 이유 |
| --- | --- |
| B.B.K.K.B.K.K. | EXP 채보의 `isConstUnknown: true` (추정 상수) |
| Ave Mujica, ビッグブリッヂの死闘 -シアトリズムFFAC Arrange- from FFV, BOW AND ARROW（アニメ・オープニングver.） | `isNew: true` (신곡 기준일 `2026-04-16` ≥ `newSongCutoff: 2025-12-10`) |
| Grab your sword, Angel dust, Club Ibuki in Break All, Warcry, No Routine, いーあるふぁんくらぶ, 華鳥風月 | 일반 곡(별칭·채보 조회 테스트용 필러) |

**국제판 미수록 테스트는 이 파일에 없는 곡으로 한다.** `third_party/chuni_penguin/database/seeds/songs.json`의 "天体観測"(BUMP OF CHICKEN [cover], `available: false`, `removed: true`)는 실제로 이 Chunimaru 목록(원본 전체 1,574곡 기준)에도 없다 — 로컬 시드에는 있지만 국제판에는 없는 실제 사례다. `songdb` 서비스가 이 곡을 "국제판 미수록"으로 걸러내는지는 이 샘플 파일에 그 제목이 없다는 사실 자체로 검증한다(샘플에 넣을 필요가 없다).

**주의**: 호출 제한(IP당 시간당 30회)이 있으니 다시 받아올 필요가 생기면 한 번만 호출하고, 바뀐 범위만 다시 고른다.
