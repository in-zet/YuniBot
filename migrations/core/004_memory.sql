-- migrations/core/004_memory.sql
CREATE TABLE player_memory (
    player_id TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT '',
    auto_save INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
