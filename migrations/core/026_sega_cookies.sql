-- migrations/core/026_sega_cookies.sql
-- clal 쿠키는 SEGA aime 로그인 쿠키라 게임 공통이다. 이름만 게임 중립으로 바꾼다(구조는 그대로).
ALTER TABLE chunithm_net_cookies RENAME TO sega_cookies;
