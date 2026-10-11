-- migrations/core/007_chunithm_net_cookies.sql
CREATE TABLE chunithm_net_cookies (
    player_id TEXT PRIMARY KEY REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    encrypted_cookie BLOB NOT NULL,
    linked_at TEXT NOT NULL,
    last_checked_at TEXT
);
