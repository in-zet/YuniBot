-- migrations/core/033_arcaea_song_art.sql
-- Arcaea Online이 기록마다 주는 공식 재킷 번호(`bg`, 그림은 https://webassets.lowiro.com/{bg}.jpg). 곡 DB(비공식 자료)에
-- 재킷이 없는 신곡에 쓰고, 일본 위키 재킷을 못 받을 때 대신 받는다(2026-11-28). 봇 전역이다.
CREATE TABLE arcaea_song_art (
    song_title TEXT PRIMARY KEY,
    bg TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
