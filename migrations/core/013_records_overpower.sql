-- migrations/core/013_records_overpower.sql
ALTER TABLE records_sync_log ADD COLUMN overpower_value REAL;
ALTER TABLE records_sync_log ADD COLUMN overpower_percentage REAL;
