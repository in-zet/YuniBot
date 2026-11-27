-- migrations/core/030_records_profile_username.sql
-- 게임별 NET 이름. 유니가 주 게임의 이름으로 부르려면 게임마다 따로 있어야 한다(sega_cookies.nickname은 로그인한 NET 하나뿐).
ALTER TABLE records_profile ADD COLUMN username TEXT;
