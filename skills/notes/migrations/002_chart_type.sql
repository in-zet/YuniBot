-- skills/notes/migrations/002_chart_type.sql
-- maimai의 STD/DX를 구분하려고 chart_type을 기본 키에 넣는다(츄니즘은 ''). 기본 키를 바꾸려 테이블을 다시 만든다.
CREATE TABLE notes_notes_new (
    player_id TEXT NOT NULL,
    game TEXT NOT NULL DEFAULT 'chunithm',
    song_title TEXT NOT NULL,
    chart_type TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL DEFAULT '',  -- 빈 문자열 = 곡 전체 메모 (결정 사항 N)
    body TEXT NOT NULL,
    public INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (player_id, game, song_title, chart_type, difficulty)
);

INSERT INTO notes_notes_new (player_id, game, song_title, chart_type, difficulty, body, public, updated_at)
SELECT player_id, game, song_title, '', difficulty, body, public, updated_at FROM notes_notes;

DROP TABLE notes_notes;
ALTER TABLE notes_notes_new RENAME TO notes_notes;
