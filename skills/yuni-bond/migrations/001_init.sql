-- skills/yuni-bond/migrations/001_init.sql
CREATE TABLE yuni_bond_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    kind TEXT NOT NULL,
    song_title TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    occurred_on TEXT NOT NULL,
    started_on TEXT NOT NULL DEFAULT '',
    UNIQUE (player_id, game, kind, song_title, difficulty, value)
);
CREATE INDEX yuni_bond_milestones_player ON yuni_bond_milestones (player_id, kind, occurred_on);

CREATE TABLE yuni_bond_promises (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    text TEXT NOT NULL,
    song_title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_on TEXT NOT NULL,
    closed_on TEXT NOT NULL DEFAULT ''
);
CREATE INDEX yuni_bond_promises_player ON yuni_bond_promises (player_id, status);
