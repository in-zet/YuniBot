-- skills/chunithm-arcade/migrations/001_init.sql
CREATE TABLE arcade_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    player_id TEXT NOT NULL REFERENCES players(id),
    arcade_id TEXT NOT NULL REFERENCES arcades(id),
    play_date TEXT NOT NULL,
    speed TEXT NOT NULL,
    field_wall_position TEXT NOT NULL,
    play_timing_offset TEXT NOT NULL,
    judge_timing_offset TEXT NOT NULL,
    guide_sound TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    read_at TEXT NOT NULL
);

CREATE INDEX arcade_settings_by_player_arcade ON arcade_settings (player_id, arcade_id, read_at);
CREATE INDEX arcade_settings_by_player_date ON arcade_settings (player_id, play_date);

CREATE TABLE arcade_privacy (
    player_id TEXT PRIMARY KEY REFERENCES players(id),
    public INTEGER NOT NULL DEFAULT 0
);
