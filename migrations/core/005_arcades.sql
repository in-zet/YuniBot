-- migrations/core/005_arcades.sql
CREATE TABLE arcades (
    id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (guild_id, name)
);

CREATE TABLE arcade_aliases (
    arcade_id TEXT NOT NULL REFERENCES arcades(id),
    player_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (player_id, alias)
);

CREATE TABLE arcade_visits (
    arcade_id TEXT NOT NULL REFERENCES arcades(id),
    player_id TEXT NOT NULL,
    game TEXT NOT NULL,
    visited_at TEXT NOT NULL
);
