-- migrations/core/022_rename_skills.sql
-- 17단계 Part 1: 스킬 이름에서 `chunithm-` 접두사를 뗐다(chunithm-account는 sega-account). 이름으로 저장된
-- 값을 새 이름으로 옮긴다. 스킬 마이그레이션 기록(schema_migrations.owner)을 같이 옮겨야 옛 이름의
-- 스킬 SQL이 새 폴더에서 다시 적용되지 않는다. 코어 마이그레이션이 스킬보다 먼저 돌므로 이 문이 먼저 실행된다.

UPDATE schema_migrations
SET owner = CASE owner
    WHEN 'chunithm-account' THEN 'sega-account'
    WHEN 'chunithm-sync' THEN 'sync'
    WHEN 'chunithm-song' THEN 'song'
    WHEN 'chunithm-report' THEN 'report'
    WHEN 'chunithm-image' THEN 'image'
    WHEN 'chunithm-session' THEN 'session'
    WHEN 'chunithm-folders' THEN 'folders'
    WHEN 'chunithm-notes' THEN 'notes'
    WHEN 'chunithm-goals' THEN 'goals'
    WHEN 'chunithm-terms' THEN 'terms'
    WHEN 'chunithm-arcade' THEN 'arcade'
    WHEN 'chunithm-settings' THEN 'settings'
    WHEN 'chunithm-updates' THEN 'updates'
    ELSE owner
END
WHERE owner IN ('chunithm-account', 'chunithm-sync', 'chunithm-song', 'chunithm-report', 'chunithm-image', 'chunithm-session', 'chunithm-folders', 'chunithm-notes', 'chunithm-goals', 'chunithm-terms', 'chunithm-arcade', 'chunithm-settings', 'chunithm-updates');

UPDATE settings_values
SET skill = CASE skill
    WHEN 'chunithm-account' THEN 'sega-account'
    WHEN 'chunithm-sync' THEN 'sync'
    WHEN 'chunithm-song' THEN 'song'
    WHEN 'chunithm-report' THEN 'report'
    WHEN 'chunithm-image' THEN 'image'
    WHEN 'chunithm-session' THEN 'session'
    WHEN 'chunithm-folders' THEN 'folders'
    WHEN 'chunithm-notes' THEN 'notes'
    WHEN 'chunithm-goals' THEN 'goals'
    WHEN 'chunithm-terms' THEN 'terms'
    WHEN 'chunithm-arcade' THEN 'arcade'
    WHEN 'chunithm-settings' THEN 'settings'
    WHEN 'chunithm-updates' THEN 'updates'
    ELSE skill
END
WHERE skill IN ('chunithm-account', 'chunithm-sync', 'chunithm-song', 'chunithm-report', 'chunithm-image', 'chunithm-session', 'chunithm-folders', 'chunithm-notes', 'chunithm-goals', 'chunithm-terms', 'chunithm-arcade', 'chunithm-settings', 'chunithm-updates');

UPDATE settings_skill_toggles
SET skill = CASE skill
    WHEN 'chunithm-account' THEN 'sega-account'
    WHEN 'chunithm-sync' THEN 'sync'
    WHEN 'chunithm-song' THEN 'song'
    WHEN 'chunithm-report' THEN 'report'
    WHEN 'chunithm-image' THEN 'image'
    WHEN 'chunithm-session' THEN 'session'
    WHEN 'chunithm-folders' THEN 'folders'
    WHEN 'chunithm-notes' THEN 'notes'
    WHEN 'chunithm-goals' THEN 'goals'
    WHEN 'chunithm-terms' THEN 'terms'
    WHEN 'chunithm-arcade' THEN 'arcade'
    WHEN 'chunithm-settings' THEN 'settings'
    WHEN 'chunithm-updates' THEN 'updates'
    ELSE skill
END
WHERE skill IN ('chunithm-account', 'chunithm-sync', 'chunithm-song', 'chunithm-report', 'chunithm-image', 'chunithm-session', 'chunithm-folders', 'chunithm-notes', 'chunithm-goals', 'chunithm-terms', 'chunithm-arcade', 'chunithm-settings', 'chunithm-updates');

UPDATE schedule_recurring
SET owner_skill = CASE owner_skill
    WHEN 'chunithm-account' THEN 'sega-account'
    WHEN 'chunithm-sync' THEN 'sync'
    WHEN 'chunithm-song' THEN 'song'
    WHEN 'chunithm-report' THEN 'report'
    WHEN 'chunithm-image' THEN 'image'
    WHEN 'chunithm-session' THEN 'session'
    WHEN 'chunithm-folders' THEN 'folders'
    WHEN 'chunithm-notes' THEN 'notes'
    WHEN 'chunithm-goals' THEN 'goals'
    WHEN 'chunithm-terms' THEN 'terms'
    WHEN 'chunithm-arcade' THEN 'arcade'
    WHEN 'chunithm-settings' THEN 'settings'
    WHEN 'chunithm-updates' THEN 'updates'
    ELSE owner_skill
END
WHERE owner_skill IN ('chunithm-account', 'chunithm-sync', 'chunithm-song', 'chunithm-report', 'chunithm-image', 'chunithm-session', 'chunithm-folders', 'chunithm-notes', 'chunithm-goals', 'chunithm-terms', 'chunithm-arcade', 'chunithm-settings', 'chunithm-updates');

UPDATE schedule_once
SET owner_skill = CASE owner_skill
    WHEN 'chunithm-account' THEN 'sega-account'
    WHEN 'chunithm-sync' THEN 'sync'
    WHEN 'chunithm-song' THEN 'song'
    WHEN 'chunithm-report' THEN 'report'
    WHEN 'chunithm-image' THEN 'image'
    WHEN 'chunithm-session' THEN 'session'
    WHEN 'chunithm-folders' THEN 'folders'
    WHEN 'chunithm-notes' THEN 'notes'
    WHEN 'chunithm-goals' THEN 'goals'
    WHEN 'chunithm-terms' THEN 'terms'
    WHEN 'chunithm-arcade' THEN 'arcade'
    WHEN 'chunithm-settings' THEN 'settings'
    WHEN 'chunithm-updates' THEN 'updates'
    ELSE owner_skill
END
WHERE owner_skill IN ('chunithm-account', 'chunithm-sync', 'chunithm-song', 'chunithm-report', 'chunithm-image', 'chunithm-session', 'chunithm-folders', 'chunithm-notes', 'chunithm-goals', 'chunithm-terms', 'chunithm-arcade', 'chunithm-settings', 'chunithm-updates');

UPDATE conversation_skills
SET skills = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(skills, '"chunithm-account"', '"sega-account"'), '"chunithm-sync"', '"sync"'), '"chunithm-song"', '"song"'), '"chunithm-report"', '"report"'), '"chunithm-image"', '"image"'), '"chunithm-session"', '"session"'), '"chunithm-folders"', '"folders"'), '"chunithm-notes"', '"notes"'), '"chunithm-goals"', '"goals"'), '"chunithm-terms"', '"terms"'), '"chunithm-arcade"', '"arcade"'), '"chunithm-settings"', '"settings"'), '"chunithm-updates"', '"updates"');
