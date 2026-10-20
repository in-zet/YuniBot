-- skills/chunithm-folders/migrations/001_init.sql
CREATE TABLE folders_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL REFERENCES players(id),
    game TEXT NOT NULL DEFAULT 'chunithm',
    name TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (player_id, game, name)
);

CREATE TABLE folders_charts (
    folder_id INTEGER NOT NULL REFERENCES folders_folders(id),
    song_title TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (folder_id, song_title, difficulty)
);
