-- migrations/core/029_change_log.sql
-- 서버 공유 데이터와 권한 태그를 "지운" 기록. 부여·추가는 행 자체에 누가·언제가 남지만(player_tags.source/granted_at,
-- *_aliases.added_by/added_at) 지우면 사라지므로, 지운 사람·시각을 여기 남긴다(CLAUDE.md "사람과 권한").
CREATE TABLE change_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,        -- 바꾼 플레이어 id (또는 'system:...')
    action TEXT NOT NULL,       -- 'tag.revoke' | 'songdb_alias.remove' | 'terms_alias.remove'
    target TEXT NOT NULL,       -- 대상(플레이어 id, 별칭 등)
    detail TEXT,                -- 보충(태그 이름, 별칭이 가리키던 값 등)
    at TEXT NOT NULL
);

CREATE INDEX idx_change_log_action_at ON change_log(action, at);
