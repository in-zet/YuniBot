-- migrations/core/002_player_tags.sql
CREATE TABLE player_tags (
    player_id TEXT NOT NULL REFERENCES players(id),
    tag TEXT NOT NULL,
    source TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    PRIMARY KEY (player_id, tag)
);
