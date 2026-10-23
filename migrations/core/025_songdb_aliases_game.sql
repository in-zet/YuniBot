-- migrations/core/025_songdb_aliases_game.sql
-- 같은 별칭이 게임마다 다른 곡을 가리킬 수 있어 곡 별칭에 게임 칸을 더한다(기존 별칭은 츄니즘).
CREATE TABLE songdb_aliases_new (
    game TEXT NOT NULL DEFAULT 'chunithm',
    alias TEXT NOT NULL,
    song_title TEXT NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (game, alias)
);

INSERT INTO songdb_aliases_new (game, alias, song_title, added_by, created_at)
SELECT 'chunithm', alias, song_title, added_by, created_at FROM songdb_aliases;

DROP TABLE songdb_aliases;
ALTER TABLE songdb_aliases_new RENAME TO songdb_aliases;
