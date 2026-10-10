-- migrations/core/001_players.sql
CREATE TABLE players (
    id TEXT PRIMARY KEY,
    primary_discord_id TEXT,
    records_public INTEGER NOT NULL DEFAULT 0,
    memos_public INTEGER NOT NULL DEFAULT 0,
    goals_public INTEGER NOT NULL DEFAULT 0,
    arcade_public INTEGER NOT NULL DEFAULT 0,
    reviews_public INTEGER NOT NULL DEFAULT 0,
    sync_report_public INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL
);

CREATE TABLE player_discord_accounts (
    discord_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(id),
    linked_at TEXT NOT NULL
);

CREATE INDEX idx_player_discord_accounts_player_id ON player_discord_accounts(player_id);
