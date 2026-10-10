-- migrations/core/006_schedules.sql
CREATE TABLE schedule_recurring (
    owner_skill TEXT NOT NULL,
    fn_name TEXT NOT NULL,
    cron TEXT NOT NULL,
    catch_up INTEGER NOT NULL,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    PRIMARY KEY (owner_skill, fn_name)
);

CREATE TABLE schedule_once (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_skill TEXT NOT NULL,
    event_name TEXT NOT NULL,
    payload TEXT NOT NULL,
    fire_at TEXT NOT NULL,
    catch_up INTEGER NOT NULL,
    fired_at TEXT
);
