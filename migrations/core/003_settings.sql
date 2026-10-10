-- migrations/core/003_settings.sql
CREATE TABLE settings_values (
    scope TEXT NOT NULL,       -- 'player' | 'guild'
    scope_id TEXT NOT NULL,    -- player_id 또는 guild_id(디스코드 서버 id 문자열)
    skill TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,       -- JSON으로 인코딩한 스칼라
    updated_by TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, scope_id, skill, name)
);

CREATE TABLE settings_skill_toggles (
    guild_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, skill)
);
