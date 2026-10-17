-- skills/chunithm-notes/migrations/001_init.sql
CREATE TABLE notes_notes (
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    song_title TEXT NOT NULL,
    difficulty TEXT NOT NULL DEFAULT '',  -- 빈 문자열 = 곡 전체 메모 (결정 사항 N)
    body TEXT NOT NULL,
    public INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, game, song_title, difficulty)
);
