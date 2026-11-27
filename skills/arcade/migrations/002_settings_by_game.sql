-- skills/arcade/migrations/002_settings_by_game.sql
-- maimai 설정도 저장하려고(2026-11-27, plan 6-8b) 츄니즘 항목 다섯 칸을 게임 칸 + 이름 붙인 항목 JSON으로 바꾼다.
-- 항목 이름(attr)은 GameProfile.settings_fields의 것이다. 기존 행은 모두 츄니즘이다.
CREATE TABLE arcade_settings_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id INTEGER NOT NULL,
    player_id TEXT NOT NULL REFERENCES players(id),
    arcade_id TEXT NOT NULL REFERENCES arcades(id),
    game TEXT NOT NULL,
    play_date TEXT NOT NULL,
    named_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    read_at TEXT NOT NULL
);

INSERT INTO arcade_settings_new (id, visit_id, player_id, arcade_id, game, play_date, named_json, raw_json, read_at)
SELECT id, visit_id, player_id, arcade_id, 'chunithm', play_date,
       json_object(
           'speed', speed, 'field_wall_position', field_wall_position, 'play_timing_offset', play_timing_offset,
           'judge_timing_offset', judge_timing_offset, 'guide_sound', guide_sound
       ),
       raw_json, read_at
FROM arcade_settings;

DROP TABLE arcade_settings;
ALTER TABLE arcade_settings_new RENAME TO arcade_settings;

CREATE INDEX arcade_settings_by_player_arcade ON arcade_settings (player_id, game, arcade_id, read_at);
CREATE INDEX arcade_settings_by_player_date ON arcade_settings (player_id, game, play_date);
