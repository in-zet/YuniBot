-- skills/folders/migrations/002_chart_type.sql
-- maimai의 STD/DX를 구분하려고 chart_type을 기본 키에 넣는다(츄니즘은 ''). 기본 키를 바꾸려 테이블을 다시 만든다.
CREATE TABLE folders_charts_new (
    folder_id INTEGER NOT NULL REFERENCES folders_folders(id),
    song_title TEXT NOT NULL,
    chart_type TEXT NOT NULL DEFAULT '',
    difficulty TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (folder_id, song_title, chart_type, difficulty)
);

INSERT INTO folders_charts_new (folder_id, song_title, chart_type, difficulty, added_at)
SELECT folder_id, song_title, '', difficulty, added_at FROM folders_charts;

DROP TABLE folders_charts;
ALTER TABLE folders_charts_new RENAME TO folders_charts;
