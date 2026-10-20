-- migrations/core/018_records_profile.sql
CREATE TABLE records_profile (
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    level INTEGER,
    reincarnation_stars INTEGER,
    character_image_url TEXT,
    character_frame_url TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, game)
);
