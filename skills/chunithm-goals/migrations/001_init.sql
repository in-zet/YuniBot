-- skills/chunithm-goals/migrations/001_init.sql
CREATE TABLE goals_goals (
    id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    kind TEXT NOT NULL,              -- rating | overpower | folder | song | freeform
    target_ref TEXT NOT NULL,        -- JSON: 폴더 정의 또는 {song_title, difficulty}, freeform이면 설명 텍스트
    target_value REAL,
    baseline_value REAL,
    created_at TEXT NOT NULL,
    due_at TEXT,
    duration_tag TEXT,               -- short | mid | long | NULL
    parent_goal_id TEXT REFERENCES goals_goals(id),
    status TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress | achieved | expired | stopped
    public INTEGER NOT NULL DEFAULT 0,
    notify_offsets TEXT               -- JSON 배열(일 단위), 저장만 함(발사는 9단계)
);

CREATE TABLE goals_progress (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL REFERENCES goals_goals(id),
    sync_id TEXT,
    recorded_at TEXT NOT NULL,
    value REAL,
    achievement_ratio REAL
);
