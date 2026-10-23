-- migrations/core/027_players_main_game.sql
-- 플레이어의 "주 게임". 말한 게임이 없을 때 이 게임으로 간다. ''이면 기본 게임(choose_game).
ALTER TABLE players ADD COLUMN main_game TEXT NOT NULL DEFAULT '';
