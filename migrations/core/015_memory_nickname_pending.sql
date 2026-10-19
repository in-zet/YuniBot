-- migrations/core/015_memory_nickname_pending.sql
ALTER TABLE player_memory ADD COLUMN nickname TEXT NOT NULL DEFAULT '';
ALTER TABLE player_memory ADD COLUMN forgotten TEXT NOT NULL DEFAULT '';

-- 자동 요약 대기 목록. 플레이어당 한 줄, 새 메시지가 오면 due_at(epoch 초)을 미룬다.
CREATE TABLE player_memory_pending (
    player_id TEXT PRIMARY KEY,
    messages TEXT NOT NULL,
    guild_id TEXT,
    channel_id TEXT,
    message_id TEXT,
    due_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
