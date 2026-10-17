-- migrations/core/014_records_sync_log_snapshot_averages.sql
-- 동기화마다 베스트30/신곡20 평균을 남겨 profile/best가 직전 동기화 대비 변화량을 보여 준다.
-- computed_rating처럼 계산하지 못한 값은 "계산 불가" 문자열로 넣는다. 이 열이 생기기 전 행은 NULL이다.
ALTER TABLE records_sync_log ADD COLUMN best30_average_snapshot TEXT;
ALTER TABLE records_sync_log ADD COLUMN new20_average_snapshot TEXT;
