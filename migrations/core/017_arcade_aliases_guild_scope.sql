-- migrations/core/017_arcade_aliases_guild_scope.sql
-- arcade_aliases의 PK가 (player_id, alias)뿐이라 guild_id가 없었다. 오락실 자체는 서버별인데
-- (arcades.UNIQUE(guild_id, name)) 별칭은 전역이라, 한 플레이어가 서버 A·B에서 같은 글자의
-- 별칭을 각각 다른 오락실에 등록하면 ON CONFLICT가 같은 행을 덮어써 한쪽 서버의 별칭이 조용히
-- 다른 서버의 오락실을 가리키게 됐다. PK에 guild_id를 더해 서버별로 분리한다.
ALTER TABLE arcade_aliases RENAME TO arcade_aliases_pre_guild_scope;

CREATE TABLE arcade_aliases (
    arcade_id TEXT NOT NULL REFERENCES arcades(id),
    guild_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, player_id, alias)
);

INSERT INTO arcade_aliases (arcade_id, guild_id, player_id, alias, created_at)
SELECT old.arcade_id, a.guild_id, old.player_id, old.alias, old.created_at
FROM arcade_aliases_pre_guild_scope old
JOIN arcades a ON a.id = old.arcade_id;

DROP TABLE arcade_aliases_pre_guild_scope;
