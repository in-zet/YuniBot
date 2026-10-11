-- migrations/core/011_songdb_aliases.sql
CREATE TABLE songdb_aliases (
    guild_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    song_title TEXT NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, alias)
);
