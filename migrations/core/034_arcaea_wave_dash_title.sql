-- migrations/core/034_arcaea_wave_dash_title.sql
-- Arcaea 곡 제목의 물결표 차이로 곡 자료와 안 이어진 기록을 곡 자료 제목으로 옮긴다(2026-11-28 실기). 앞으로는
-- services/arcaea/songdb.py의 title_key가 물결표 모양을 같게 본다. 이 곡 하나만 어긋나 있었다.
UPDATE records_pb SET song_title = 'キャラメルポップコーンたべたいよ～'
    WHERE game = 'arcaea' AND song_title = 'キャラメルポップコーンたべたいよ〜';
UPDATE play_counts SET song_key = 'キャラメルポップコーンたべたいよ～'
    WHERE game = 'arcaea' AND song_key = 'キャラメルポップコーンたべたいよ〜';
UPDATE arcaea_song_art SET song_title = 'キャラメルポップコーンたべたいよ～'
    WHERE song_title = 'キャラメルポップコーンたべたいよ〜';
