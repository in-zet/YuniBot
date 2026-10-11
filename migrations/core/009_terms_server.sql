-- migrations/core/009_terms_server.sql
CREATE TABLE terms_server (
    id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    term TEXT NOT NULL,
    meaning TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    suggested_by TEXT NOT NULL REFERENCES players(id),
    status TEXT NOT NULL DEFAULT 'suggested',
    reviewed_by TEXT REFERENCES players(id),
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);
