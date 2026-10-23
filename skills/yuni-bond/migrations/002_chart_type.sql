-- skills/yuni-bond/migrations/002_chart_type.sql
-- maimai의 STD/DX를 구분하려고 이정표의 채보에 chart_type을 더하고 UNIQUE에 넣는다(츄니즘은 '').
CREATE TABLE yuni_bond_milestones_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    kind TEXT NOT NULL,
    song_title TEXT NOT NULL DEFAULT '',
    chart_type TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL DEFAULT '',
    value TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    occurred_on TEXT NOT NULL,
    started_on TEXT NOT NULL DEFAULT '',
    UNIQUE (player_id, game, kind, song_title, chart_type, difficulty, value)
);

INSERT INTO yuni_bond_milestones_new (
    id, player_id, game, kind, song_title, chart_type, difficulty, value, label, occurred_on, started_on
)
SELECT id, player_id, game, kind, song_title, '', difficulty, value, label, occurred_on, started_on
FROM yuni_bond_milestones;

DROP TABLE yuni_bond_milestones;
ALTER TABLE yuni_bond_milestones_new RENAME TO yuni_bond_milestones;
CREATE INDEX yuni_bond_milestones_player ON yuni_bond_milestones (player_id, kind, occurred_on);
