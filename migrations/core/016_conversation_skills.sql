-- migrations/core/016_conversation_skills.sql
-- 유니가 보낸 메시지마다, 그 답을 만들 때 펼쳐져 있던 스킬 목록(오래 안 쓴 순서). 그 메시지에 단 답장이
-- 같은 스킬을 펼친 채로 시작하게 한다. 오래된 행은 폴링 루프가 지운다.
CREATE TABLE conversation_skills (
    message_id TEXT PRIMARY KEY,
    skills TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX conversation_skills_created_at ON conversation_skills (created_at);
