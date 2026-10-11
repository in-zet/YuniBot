-- migrations/core/008_records.sql
CREATE TABLE records_sessions (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    play_date TEXT NOT NULL,
    estimated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (player_id, game, play_date)
);

CREATE TABLE records_pb (
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    song_title TEXT NOT NULL,
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
    PRIMARY KEY (player_id, game, song_title, difficulty)
);

CREATE TABLE records_sync_log (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    synced_at TEXT NOT NULL,
    nickname TEXT,
    displayed_rating TEXT,
    computed_rating TEXT NOT NULL,
    rating_mismatch INTEGER NOT NULL DEFAULT 0,
    is_first_sync INTEGER NOT NULL DEFAULT 0
);
