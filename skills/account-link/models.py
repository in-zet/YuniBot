# skills/account-link/models.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import aiosqlite


@dataclass(slots=True, kw_only=True)
class LinkCode:
    code: str
    issuer_player_id: str
    issuer_discord_id: str
    joiner_discord_id: str | None
    status: str
    issuer_confirmed: bool
    joiner_confirmed: bool
    discard_agreed: bool
    expires_at: str


def _row_to_code(row: aiosqlite.Row) -> LinkCode:
    return LinkCode(
        code=row["code"],
        issuer_player_id=row["issuer_player_id"],
        issuer_discord_id=row["issuer_discord_id"],
        joiner_discord_id=row["joiner_discord_id"],
        status=row["status"],
        issuer_confirmed=bool(row["issuer_confirmed"]),
        joiner_confirmed=bool(row["joiner_confirmed"]),
        discard_agreed=bool(row["discard_agreed"]),
        expires_at=row["expires_at"],
    )


async def create_code(
    conn: aiosqlite.Connection, *, code: str, issuer_player_id: str, issuer_discord_id: str,
    now: datetime, ttl: timedelta,
) -> None:
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "INSERT INTO account_link_codes "
        "(code, issuer_player_id, issuer_discord_id, status, created_at, expires_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?)",
        (code, issuer_player_id, issuer_discord_id, now.isoformat(), (now + ttl).isoformat()),
    )
    await conn.commit()


async def get_code(conn: aiosqlite.Connection, code: str) -> LinkCode | None:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute("SELECT * FROM account_link_codes WHERE code = ?", (code,))
    row = await cursor.fetchone()
    return _row_to_code(row) if row else None


async def mark_joined(
    conn: aiosqlite.Connection, code: str, *, joiner_discord_id: str, discard_agreed: bool = False
) -> bool:
    """pending → joined 전이를 원자적으로 시도한다. 두 참여자가 동시에 같은 코드로 들어와도
    SELECT로 status를 미리 확인하고 나중에 UPDATE하는 식(확인-행동 사이에 await가 끼어드는
    TOCTOU)으로는 막을 수 없다 — WHERE status = 'pending'까지 포함한 UPDATE 한 문장만이
    원자적이다. 돌려주는 bool은 "이 호출이 실제로 그 전이를 해냈는가"다(아니면 이미 다른
    참여자가 가져갔다는 뜻).

    discard_agreed도 같은 UPDATE 문에서 함께 쓴다 — 예전에는 버리고 연결 버튼이 이걸 따로
    된 UPDATE로 미리 써 놓았는데, 같은 유출된 코드로 두 참여자가 경쟁하면 "버리는 데
    동의했다"는 표시가 실제로 이 전이를 가져간(mark_joined에서 이긴) 참여자가 아니라 먼저
    클릭만 한 참여자 쪽에 붙어 버릴 수 있었다. 전이를 가져가는 이 한 문장에 같이 실어야
    누가 이 코드를 "정말로" 가져갔는지와 묶인다."""
    cursor = await conn.execute(
        "UPDATE account_link_codes SET joiner_discord_id = ?, status = 'joined', discard_agreed = ? "
        "WHERE code = ? AND status = 'pending'",
        (joiner_discord_id, 1 if discard_agreed else 0, code),
    )
    await conn.commit()
    return cursor.rowcount == 1


async def mark_finalizing(conn: aiosqlite.Connection, code: str) -> bool:
    """양쪽이 다 확인한 뒤 실제로 계정을 옮기고 데이터를 지우는 마무리 단계를 "한 번만" 하는
    주장(claim)을 원자적으로 시도한다. mark_joined와 같은 이유로 — 확인 버튼 콜백은 매
    await마다 다른 클릭이 끼어들 수 있는 별도의 asyncio 태스크라서, 상태를 먼저 읽고 나중에
    파괴적인 작업을 하는 방식은 두 번째 클릭이 끼어들면 안전하지 않다. 이 UPDATE 한 문장만이
    "내가 지금 이 코드의 마무리를 가져간다"를 원자적으로 보장한다."""
    cursor = await conn.execute(
        "UPDATE account_link_codes SET status = 'finalizing' "
        "WHERE code = ? AND status NOT IN ('finalizing', 'completed')",
        (code,),
    )
    await conn.commit()
    return cursor.rowcount == 1


async def set_confirmed(conn: aiosqlite.Connection, code: str, *, side: str) -> None:
    column = "issuer_confirmed" if side == "issuer" else "joiner_confirmed"
    await conn.execute(f"UPDATE account_link_codes SET {column} = 1 WHERE code = ?", (code,))
    await conn.commit()


async def mark_status(conn: aiosqlite.Connection, code: str, *, status: str) -> None:
    await conn.execute("UPDATE account_link_codes SET status = ? WHERE code = ?", (status, code))
    await conn.commit()


async def wrong_attempt_count(conn: aiosqlite.Connection, discord_id: str, *, now: datetime) -> int:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        "SELECT wrong_count, locked_until FROM account_link_attempts WHERE discord_id = ?", (discord_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        return 0
    if row["locked_until"]:
        if row["locked_until"] > now.isoformat():
            return 999  # 잠겨 있는 동안은 횟수와 무관하게 막힌 것으로 본다
        return 0  # 잠금 기간이 지났으면 새로 다섯 번을 준다(안 그러면 영구 잠금이 된다)
    return row["wrong_count"]


async def record_wrong_attempt(
    conn: aiosqlite.Connection, discord_id: str, *, now: datetime, lock_after: int, cooldown: timedelta
) -> int:
    conn.row_factory = aiosqlite.Row
    cursor = await conn.execute(
        "SELECT wrong_count, locked_until FROM account_link_attempts WHERE discord_id = ?", (discord_id,)
    )
    row = await cursor.fetchone()
    # 잠금 기간이 지났으면 쌓인 횟수를 이어받지 않는다(wrong_attempt_count와 같은 기준) —
    # 안 그러면 다음 한 번만 틀려도 바로 다시 잠긴다(사실상 영구 잠금).
    lock_expired = bool(row and row["locked_until"] and row["locked_until"] <= now.isoformat())
    previous_count = 0 if (row is None or lock_expired) else row["wrong_count"]
    count = previous_count + 1
    locked_until = (now + cooldown).isoformat() if count >= lock_after else None
    await conn.execute(
        "INSERT INTO account_link_attempts (discord_id, wrong_count, locked_until) VALUES (?, ?, ?) "
        "ON CONFLICT (discord_id) DO UPDATE SET wrong_count = excluded.wrong_count, "
        "locked_until = excluded.locked_until",
        (discord_id, count, locked_until),
    )
    await conn.commit()
    return count


async def reset_wrong_attempts(conn: aiosqlite.Connection, discord_id: str) -> None:
    await conn.execute(
        "INSERT INTO account_link_attempts (discord_id, wrong_count, locked_until) VALUES (?, 0, NULL) "
        "ON CONFLICT (discord_id) DO UPDATE SET wrong_count = 0, locked_until = NULL",
        (discord_id,),
    )
    await conn.commit()
