-- migrations/core/020_aliases_global.sql
-- 별칭은 전부 봇 전역이다(2026-10-21 사용자 결정): 서버·플레이어마다 따로 두지 않고, 한 번 확인받아
-- 저장하면 모두에게 적용된다. 곡 별칭·오락실 별칭의 범위(guild/player)를 없앤다. 오락실 목록(arcades)도 전역으로 쓴다 — guild_id 열은
-- 남기되 쓰지 않는다(빈 문자열).
-- 같은 별칭이 여러 범위에 있었으면 가장 먼저 등록된 것만 남긴다.
ALTER TABLE songdb_aliases RENAME TO songdb_aliases_per_guild;

CREATE TABLE songdb_aliases (
    alias TEXT PRIMARY KEY,
    song_title TEXT NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT OR IGNORE INTO songdb_aliases (alias, song_title, added_by, created_at)
SELECT alias, song_title, added_by, created_at FROM songdb_aliases_per_guild ORDER BY created_at;

DROP TABLE songdb_aliases_per_guild;

ALTER TABLE arcade_aliases RENAME TO arcade_aliases_per_player;

CREATE TABLE arcade_aliases (
    alias TEXT PRIMARY KEY,
    arcade_id TEXT NOT NULL REFERENCES arcades(id),
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT OR IGNORE INTO arcade_aliases (alias, arcade_id, added_by, created_at)
SELECT lower(alias), arcade_id, player_id, created_at FROM arcade_aliases_per_player ORDER BY created_at;

DROP TABLE arcade_aliases_per_player;
