CREATE TABLE account_link_codes (
    code TEXT PRIMARY KEY,
    issuer_player_id TEXT NOT NULL,
    issuer_discord_id TEXT NOT NULL,
    joiner_discord_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    issuer_confirmed INTEGER NOT NULL DEFAULT 0,
    joiner_confirmed INTEGER NOT NULL DEFAULT 0,
    discard_agreed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE account_link_attempts (
    discord_id TEXT PRIMARY KEY,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT
);
