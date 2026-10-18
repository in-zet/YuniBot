-- skills/chunithm-updates/migrations/001_init.sql
CREATE TABLE updates_notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL DEFAULT 'chunithm',
    region TEXT NOT NULL,       -- 'intl' | 'jp'
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    posted_at TEXT NOT NULL,    -- 공지가 게시된 날짜(ISO, 시각 없음)
    update_date TEXT,           -- 제목에서 뽑은 업데이트 날짜(ISO, 시각 없음). 못 뽑으면 NULL
    version_name TEXT,          -- 제목의 『...』에서 뽑은 버전명. 없으면 NULL
    schedule_id INTEGER,        -- update_date 당일 10시 알림의 schedule_once 행 id
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (region, url)
);

CREATE TABLE updates_song_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL DEFAULT 'chunithm',
    region TEXT NOT NULL,       -- 'intl' | 'jp'
    checked_at TEXT NOT NULL,
    song_ids_json TEXT NOT NULL,   -- 이 시점 전체 곡 목록({id: title}) — 다음 비교의 기준
    added_json TEXT NOT NULL,      -- 이전 저장본과 비교해 추가된 곡([{id, title}])
    removed_json TEXT NOT NULL     -- 이전 저장본과 비교해 삭제된 곡([{id, title}])
);
