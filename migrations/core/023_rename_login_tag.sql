-- migrations/core/023_rename_login_tag.sql
-- 17단계 Part 1: 세가 계정 연결 상태 태그가 게임 공통이 돼 이름이 chunithm-linked에서 sega-linked로 바뀌었다.
UPDATE player_tags SET tag = 'sega-linked' WHERE tag = 'chunithm-linked';
