-- skills/chunithm-session/migrations/002_reviews.sql
CREATE TABLE session_reviews (
    session_id TEXT PRIMARY KEY REFERENCES session_reports(session_id),
    player_id TEXT NOT NULL REFERENCES players(id),
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    messages TEXT NOT NULL,
    is_public INTEGER NOT NULL DEFAULT 0
);
