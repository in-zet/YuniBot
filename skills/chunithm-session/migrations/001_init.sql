-- skills/chunithm-session/migrations/001_init.sql
CREATE TABLE session_reports (
    session_id TEXT PRIMARY KEY REFERENCES records_sessions(id),
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    play_date TEXT NOT NULL,
    estimated INTEGER NOT NULL DEFAULT 0,
    rendered_text TEXT NOT NULL,
    sections_data TEXT NOT NULL,
    yuni_line TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
