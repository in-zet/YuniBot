-- skills/chunithm-goals/migrations/002_notify_schedule.sql
ALTER TABLE goals_goals ADD COLUMN guild_id TEXT;  -- 목표를 만들 때의 ctx.guild_id. DM이면 NULL(알림은 DM으로 간다)

CREATE TABLE goals_notify_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id TEXT NOT NULL REFERENCES goals_goals(id),
    schedule_id INTEGER NOT NULL,  -- core schedule_once 행 id(Scheduler.cancel_once에 쓴다)
    kind TEXT NOT NULL             -- reminder | final_deadline
);
