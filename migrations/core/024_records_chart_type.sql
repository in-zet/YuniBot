-- migrations/core/024_records_chart_type.sql
-- maimai는 같은 곡에 STD/DX 채보가 따로 있어 (곡, 난이도)만으로는 채보가 정해지지 않는다 → chart_type을
-- 기본 키에 넣는다(츄니즘은 ''). dx_score는 maimai의 DX 스코어(츄니즘은 NULL).
-- 기본 키를 바꾸려면 테이블을 다시 만들어야 해 새 테이블에 복사한 뒤 바꿔 끼운다(2026-10-23 사용자 결정).
CREATE TABLE records_pb_new (
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    song_title TEXT NOT NULL,
    chart_type TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL,
    score INTEGER NOT NULL,
    rank TEXT NOT NULL,
    clear_lamp TEXT NOT NULL,
    combo_lamp TEXT NOT NULL,
    chain_lamp TEXT NOT NULL,
    const REAL,
    rating TEXT NOT NULL,
    session_id TEXT REFERENCES records_sessions(id),
    updated_at TEXT NOT NULL,
    dx_score INTEGER,
    PRIMARY KEY (player_id, game, song_title, chart_type, difficulty)
);

INSERT INTO records_pb_new (
    player_id, game, song_title, chart_type, difficulty, score, rank,
    clear_lamp, combo_lamp, chain_lamp, const, rating, session_id, updated_at
)
SELECT
    player_id, game, song_title, '', difficulty, score, rank,
    clear_lamp, combo_lamp, chain_lamp, const, rating, session_id, updated_at
FROM records_pb;

DROP TABLE records_pb;
ALTER TABLE records_pb_new RENAME TO records_pb;
