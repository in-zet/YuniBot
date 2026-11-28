-- migrations/core/031_play_counts.sql
-- 채보 플레이 횟수(플카). NET 목록에는 없고 곡 상세에만 있어 동기화 뒤에 배경에서 곡마다 받아 둔다(2026-11-28 사용자 결정).
-- song_key는 NET 서비스가 곡을 가리키는 값: CHUNITHM musicId(글자로), maimai 제목. chart_type은 maimai STD/DX(CHUNITHM은 '').
-- counts_json은 난이도 → 횟수. 행이 있는데 난이도가 없으면 0회, 행이 없으면 모름이다.
CREATE TABLE play_counts (
    player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game TEXT NOT NULL,
    song_key TEXT NOT NULL,
    chart_type TEXT NOT NULL DEFAULT '',
    counts_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (player_id, game, song_key, chart_type)
);

-- 마지막 수집 상태. total_plays는 NET 플레이어 정보의 총 플레이 횟수(크레딧), newest_play_at은 그때 최근 목록의 가장 새 판.
-- complete=0이면 다음 수집은 전체를 다시 받는다(처음, 놓친 판, 못 읽은 곡이 있었을 때).
CREATE TABLE play_count_state (
    player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game TEXT NOT NULL,
    total_plays INTEGER,
    newest_play_at TEXT,
    complete INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, game)
);
