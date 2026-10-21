-- migrations/core/021_terms_aliases.sql
-- 난이도·랭크·램프·작곡가의 확인받은 별칭. kind는 'difficulty' | 'rank' | 'lamp' | 'artist',
-- canonical은 그 종류의 정식 이름(작곡가는 곡 DB에 있는 표기 그대로)이다.
CREATE TABLE terms_aliases (
    kind TEXT NOT NULL,
    alias TEXT NOT NULL,
    canonical TEXT NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (kind, alias)
);
