-- migrations/core/019_records_session_state.sql
-- 같은 날(세션)에 여러 번 동기화해도 세션 리포트가 하루치를 합쳐 보여 주도록, 세션마다 지금까지
-- 누적된 비교 데이터(세션 시작 시점의 레이팅·평균, 채보별 갱신, 미갱신 플레이, 프레임 이탈)를
-- JSON 한 덩어리로 둔다. 다음 동기화가 이 위에 더한다.
CREATE TABLE records_session_state (
    session_id TEXT PRIMARY KEY REFERENCES records_sessions(id) ON DELETE CASCADE,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
