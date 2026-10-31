# services/common/audit.py
"""서버 공유 데이터·권한 태그를 지운 기록(`change_log`). 부여·추가는 행에 누가·언제가 남지만 지우면 사라진다."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite


async def record_removal(
    conn: aiosqlite.Connection, *, actor: str, action: str, target: str, detail: str | None = None
) -> None:
    """호출자의 삭제 문장과 같은 커밋에 실리도록 커밋하지 않는다 — 호출자가 삭제 뒤 커밋한다."""
    await conn.execute(
        "INSERT INTO change_log (actor, action, target, detail, at) VALUES (?, ?, ?, ?, ?)",
        (actor, action, target, detail, datetime.now(UTC).isoformat()),
    )
