-- migrations/core/028_terms_aliases_game.sql
-- 난이도·랭크·램프·작곡가 별칭에 게임 칸을 더한다. 같은 말이 게임마다 다른 뜻일 수 있다(SSS는 두 게임 점수가 다르다).
-- 기존 별칭은 츄니즘.
CREATE TABLE terms_aliases_new (
    game TEXT NOT NULL DEFAULT 'chunithm',
    kind TEXT NOT NULL,
    alias TEXT NOT NULL,
    canonical TEXT NOT NULL,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (game, kind, alias)
);

INSERT INTO terms_aliases_new (game, kind, alias, canonical, added_by, created_at)
SELECT 'chunithm', kind, alias, canonical, added_by, created_at FROM terms_aliases;

DROP TABLE terms_aliases;
ALTER TABLE terms_aliases_new RENAME TO terms_aliases;
