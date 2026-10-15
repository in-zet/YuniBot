-- migrations/core/012_llm_usage.sql
CREATE TABLE llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,              -- 'agent' | 'summary' 등 호출 용도
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_llm_usage_guild_month ON llm_usage(guild_id, created_at);
