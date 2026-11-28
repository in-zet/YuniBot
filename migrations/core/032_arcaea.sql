-- migrations/core/032_arcaea.sql
-- Arcaea(19단계 2단계, 2026-11-28 사용자 결정). Arcaea Online 로그인 쿠키(`sid`)는 SEGA 계정과 따로라 테이블을 따로 둔다.
-- 모양은 sega_cookies와 같다(services/common/account_cookies.py가 둘을 같은 코드로 다룬다).
CREATE TABLE arcaea_sessions (
    player_id TEXT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    encrypted_cookie BLOB NOT NULL,
    linked_at TEXT NOT NULL,
    last_checked_at TEXT,
    nickname TEXT
);

-- Arcaea Online 베스트 50의 곡별 포텐셜에서 거꾸로 낸 상수. 곡 DB(비공식 자료)에 없거나 다른 채보를 덮는다. 봇 전역이다.
-- song_title은 곡 DB 제목(곡 DB에 없는 곡이면 Arcaea Online의 제목)이다.
CREATE TABLE arcaea_const_observed (
    song_title TEXT NOT NULL,
    difficulty TEXT NOT NULL,
    const REAL NOT NULL,
    artist TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (song_title, difficulty)
);
