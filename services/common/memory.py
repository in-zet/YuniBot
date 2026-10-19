from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

logger = logging.getLogger(__name__)

MAX_LENGTH = 1500
MAX_NICKNAME_LENGTH = 20
MAX_FORGOTTEN_LENGTH = 500
# 마지막 대화 뒤 이만큼 조용하면 요약한다. 그 안에 다시 말하면 기다림이 새로 시작된다.
SUMMARY_DELAY_SECONDS = 300
MAX_PENDING_MESSAGES = 20
MAX_PENDING_MESSAGE_CHARS = 500
MAX_SUMMARY_ATTEMPTS = 3
# 호칭을 가리키는 말. forget의 what이 이 말이면 호칭 칸을 지운다.
NICKNAME_KEYWORD = "호칭"


_NICKNAME_BLOCKED = "@<>`"


def clean_nickname(raw: str) -> str:
    """호칭 한 칸에 넣을 수 있게 다듬는다. 줄바꿈·공백 겹침을 없애고, 프롬프트와 디스코드 멘션에
    쓰이는 문자(`@ < > 백틱)를 뺀다. 비면 빈 문자열이다."""
    kept = "".join(ch for ch in raw if ch not in _NICKNAME_BLOCKED and (ch.isprintable() or ch.isspace()))
    return " ".join(kept.split())[:MAX_NICKNAME_LENGTH].strip()


_MENTION_TOKEN = re.compile(r"<@[!&]?\d+>")


def strip_mentions(content: str) -> str:
    """디스코드 멘션 토큰을 빼고 앞뒤 공백을 다듬는다. 요약 대기 목록에는 사람이 쓴 말만 넣는다."""
    return _MENTION_TOKEN.sub("", content).strip()


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _line_matches(line: str, what: str) -> bool:
    """같은 줄이거나 한쪽이 다른 쪽을 포함하면 맞는 줄로 본다(공백·대소문자 무시)."""
    a, b = _normalize(line), _normalize(what)
    return bool(a) and bool(b) and (a == b or b in a or a in b)


def _trim_front(lines: list[str], limit: int) -> list[str]:
    """줄을 합쳐 limit자를 넘으면 맨 앞(오래된) 줄부터 버린다. 한 줄이 혼자 넘으면 그 줄을 자른다."""
    kept = list(lines)
    while len("\n".join(kept)) > limit and len(kept) > 1:
        kept.pop(0)
    if kept and len(kept[0]) > limit:
        kept[0] = kept[0][:limit]
    return kept


@dataclass(slots=True, kw_only=True, frozen=True)
class MemoryState:
    content: str = ""
    nickname: str = ""
    forgotten: str = ""
    auto_save: bool = True


@dataclass(slots=True, kw_only=True, frozen=True)
class PendingMessage:
    id: str
    text: str


@dataclass(slots=True, kw_only=True, frozen=True)
class PendingSummary:
    player_id: str
    messages: list[PendingMessage]
    guild_id: str | None
    channel_id: str | None
    message_id: str | None
    due_at: float
    attempts: int


@dataclass(slots=True, kw_only=True, frozen=True)
class MemoryMerge:
    """요약 모델이 돌려준 새 메모리. nickname이 None이면 호칭은 그대로 둔다."""

    content: str
    nickname: str | None = None


Merger = Callable[[MemoryState, PendingSummary], Awaitable[MemoryMerge | None]]
OnChanged = Callable[[PendingSummary], Awaitable[None]]


@dataclass(slots=True, kw_only=True, frozen=True)
class RememberResult:
    content_saved: str | None
    nickname_saved: str | None


class MemoryService:
    """플레이어 메모리 저장소: 본문 한 덩어리 + 호칭 한 칸 + 지운 항목(`forgotten`), 자동 요약 대기 목록.
    LLM 호출은 여기 없다 — 요약을 만드는 일은 `flush_due`에 넘기는 merge 콜러블이 한다."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ---------------------------------------------------------------- 읽기

    async def get_state(self, player_id: str) -> MemoryState:
        cursor = await self._conn.execute(
            "SELECT content, nickname, forgotten, auto_save FROM player_memory WHERE player_id = ?",
            (player_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return MemoryState()
        return MemoryState(content=row[0], nickname=row[1], forgotten=row[2], auto_save=bool(row[3]))

    async def get(self, player_id: str) -> str:
        return (await self.get_state(player_id)).content

    async def get_nickname(self, player_id: str) -> str:
        return (await self.get_state(player_id)).nickname

    async def get_auto_save(self, player_id: str) -> bool:
        return (await self.get_state(player_id)).auto_save

    # ---------------------------------------------------------------- 쓰기

    async def _write(
        self,
        player_id: str,
        *,
        content: str | None = None,
        nickname: str | None = None,
        forgotten: str | None = None,
    ) -> None:
        """넘긴 칸만 바꾼다(없던 플레이어면 행을 만든다)."""
        state = await self.get_state(player_id)
        await self._conn.execute(
            "INSERT INTO player_memory (player_id, content, nickname, forgotten, auto_save, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (player_id) DO UPDATE SET content = excluded.content, nickname = excluded.nickname, "
            "forgotten = excluded.forgotten, updated_at = excluded.updated_at",
            (
                player_id,
                state.content if content is None else content,
                state.nickname if nickname is None else nickname,
                state.forgotten if forgotten is None else forgotten,
                1 if state.auto_save else 0,
                datetime.now(UTC).isoformat(),
            ),
        )
        await self._conn.commit()

    async def set(self, player_id: str, content: str) -> None:
        if len(content) > MAX_LENGTH:
            raise ValueError(f"메모리는 {MAX_LENGTH}자를 넘을 수 없다({len(content)}자)")
        await self._write(player_id, content=content)

    async def set_nickname(self, player_id: str, nickname: str) -> str:
        """다듬은 호칭을 저장하고 돌려준다. 다듬고 나서 비면 지운 것과 같다."""
        cleaned = clean_nickname(nickname)
        await self._write(player_id, nickname=cleaned)
        return cleaned

    async def clear(self, player_id: str) -> None:
        """본문과 호칭을 비우고 대기 중인 요약을 버린다. 지운 항목(`forgotten`)과 자동저장 설정은 둔다."""
        await self._write(player_id, content="", nickname="")
        await self._conn.execute("DELETE FROM player_memory_pending WHERE player_id = ?", (player_id,))
        await self._conn.commit()

    async def set_auto_save(self, player_id: str, enabled: bool) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO player_memory (player_id, auto_save, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT (player_id) DO UPDATE SET auto_save = excluded.auto_save, "
            "updated_at = excluded.updated_at",
            (player_id, 1 if enabled else 0, now),
        )
        if not enabled:
            await self._conn.execute("DELETE FROM player_memory_pending WHERE player_id = ?", (player_id,))
        await self._conn.commit()

    async def remember(
        self, player_id: str, *, content: str | None = None, nickname: str | None = None
    ) -> RememberResult:
        """플레이어가 직접 기억해 달라고 한 것을 바로 저장한다. 지운 항목에 있던 것이면 거기서 뺀다."""
        state = await self.get_state(player_id)
        forgotten = _lines(state.forgotten)
        new_content: str | None = None
        saved_content: str | None = None
        if content is not None and content.strip():
            line = " ".join(content.split())
            lines = _lines(state.content)
            if all(_normalize(line) != _normalize(existing) for existing in lines):
                lines.append(line)
            new_content = "\n".join(_trim_front(lines, MAX_LENGTH))
            saved_content = line
            forgotten = [f for f in forgotten if not _line_matches(f, line)]
        saved_nickname: str | None = None
        if nickname is not None and clean_nickname(nickname):
            saved_nickname = clean_nickname(nickname)
            forgotten = [f for f in forgotten if not _line_matches(f, saved_nickname)]
        await self._write(
            player_id, content=new_content, nickname=saved_nickname, forgotten="\n".join(forgotten),
        )
        return RememberResult(content_saved=saved_content, nickname_saved=saved_nickname)

    # ---------------------------------------------------------------- 지우기

    async def find_forget(self, player_id: str, what: str | None) -> list[str]:
        """`what`에 맞는 지울 항목(본문 줄, 호칭이면 "호칭: ○○")을 쓰지 않고 찾는다. `what`이 없으면 전부다."""
        state = await self.get_state(player_id)
        lines = _lines(state.content)
        if what is None or not what.strip():
            found = list(lines)
            if state.nickname:
                found.append(f"{NICKNAME_KEYWORD}: {state.nickname}")
            return found
        found = [line for line in lines if _line_matches(line, what)]
        if state.nickname and _normalize(what) in (_normalize(NICKNAME_KEYWORD), _normalize(state.nickname)):
            found.append(f"{NICKNAME_KEYWORD}: {state.nickname}")
        return found

    async def forget(self, player_id: str, what: str | None) -> list[str]:
        """맞는 항목을 지우고 지운 항목을 돌려준다. 지운 문장은 `forgotten`에 남겨 요약이 되살리지 않게 한다."""
        state = await self.get_state(player_id)
        found = await self.find_forget(player_id, what)
        if not found:
            return []
        lines = _lines(state.content)
        nickname_item = f"{NICKNAME_KEYWORD}: {state.nickname}" if state.nickname else None
        removed_lines = [item for item in found if item != nickname_item]
        kept = [line for line in lines if line not in removed_lines]
        nickname = "" if nickname_item is not None and nickname_item in found else state.nickname
        # 호칭 자체는 forgotten에 넣지 않는다 — 호칭을 지운 뒤 같은 호칭을 다시 정하는 것은 막지 않는다.
        forgotten = _trim_front([*_lines(state.forgotten), *removed_lines], MAX_FORGOTTEN_LENGTH)
        await self._write(player_id, content="\n".join(kept), nickname=nickname, forgotten="\n".join(forgotten))
        if what is None or not what.strip():
            await self._conn.execute("DELETE FROM player_memory_pending WHERE player_id = ?", (player_id,))
            await self._conn.commit()
        return found

    # ---------------------------------------------------------------- 자동 요약 대기 목록

    async def enqueue(
        self,
        player_id: str,
        *,
        messages: list[PendingMessage],
        guild_id: str | None,
        channel_id: str | None,
        message_id: str | None,
        now: float,
    ) -> bool:
        """본인이 쓴 메시지를 요약 대기 목록에 더하고 요약 시각을 `now`+5분으로 미룬다.
        자동저장이 꺼져 있거나 더할 메시지가 없으면 아무것도 하지 않는다."""
        if not messages or not await self.get_auto_save(player_id):
            return False
        existing = await self._pending_messages(player_id)
        known = {m.id for m in existing}
        merged = [
            *existing,
            *(
                PendingMessage(id=m.id, text=m.text[:MAX_PENDING_MESSAGE_CHARS])
                for m in messages
                if m.id not in known and m.text.strip()
            ),
        ][-MAX_PENDING_MESSAGES:]
        if not merged:
            return False
        payload = json.dumps([{"id": m.id, "text": m.text} for m in merged], ensure_ascii=False)
        await self._conn.execute(
            "INSERT INTO player_memory_pending (player_id, messages, guild_id, channel_id, message_id, due_at, attempts) "
            "VALUES (?, ?, ?, ?, ?, ?, 0) "
            "ON CONFLICT (player_id) DO UPDATE SET messages = excluded.messages, guild_id = excluded.guild_id, "
            "channel_id = excluded.channel_id, message_id = excluded.message_id, due_at = excluded.due_at, attempts = 0",
            (player_id, payload, guild_id, channel_id, message_id, now + SUMMARY_DELAY_SECONDS),
        )
        await self._conn.commit()
        return True

    async def _pending_messages(self, player_id: str) -> list[PendingMessage]:
        cursor = await self._conn.execute(
            "SELECT messages FROM player_memory_pending WHERE player_id = ?", (player_id,)
        )
        row = await cursor.fetchone()
        return [] if row is None else _decode_messages(row[0])

    async def due_pending(self, *, now: float) -> list[PendingSummary]:
        cursor = await self._conn.execute(
            "SELECT player_id, messages, guild_id, channel_id, message_id, due_at, attempts "
            "FROM player_memory_pending WHERE due_at <= ? ORDER BY due_at",
            (now,),
        )
        return [
            PendingSummary(
                player_id=row[0], messages=_decode_messages(row[1]), guild_id=row[2], channel_id=row[3],
                message_id=row[4], due_at=row[5], attempts=row[6],
            )
            for row in await cursor.fetchall()
        ]

    async def flush_due(self, *, now: float, merge: Merger, on_changed: OnChanged | None = None) -> int:
        """시각이 된 대기 목록을 요약해 메모리에 반영하고 반영한 건수를 돌려준다. 한 플레이어의 실패가
        다른 플레이어를 막지 않는다. 요약이 실패하면 5분 뒤 다시 하고, 세 번 실패하면 버린다."""
        changed_count = 0
        for pending in await self.due_pending(now=now):
            try:
                if await self._flush_one(pending, now=now, merge=merge, on_changed=on_changed):
                    changed_count += 1
            except Exception:
                logger.warning("플레이어 %s의 메모리 요약 반영 실패", pending.player_id, exc_info=True)
        return changed_count

    async def _flush_one(
        self, pending: PendingSummary, *, now: float, merge: Merger, on_changed: OnChanged | None
    ) -> bool:
        state = await self.get_state(pending.player_id)
        if not state.auto_save:
            await self._drop_pending(pending)
            return False
        try:
            merged = await merge(state, pending)
        except Exception:
            logger.warning("플레이어 %s의 메모리 요약 호출 실패", pending.player_id, exc_info=True)
            merged = None
        if merged is None:
            await self._retry_or_drop(pending, now=now)
            return False

        content, nickname = self._apply_merge(state, merged)
        changed = content != state.content or nickname != state.nickname
        if changed:
            await self._write(pending.player_id, content=content, nickname=nickname)
        await self._drop_pending(pending)
        if changed and on_changed is not None:
            try:
                await on_changed(pending)
            except Exception:
                logger.warning("메모리 변경 표시 실패", exc_info=True)
        return changed

    def _apply_merge(self, state: MemoryState, merged: MemoryMerge) -> tuple[str, str]:
        """모델이 돌려준 메모리를 코드로 다시 검사한다: 지운 항목은 빼고, 중복을 없애고, 상한에 맞춘다."""
        forgotten = _lines(state.forgotten)
        kept: list[str] = []
        for line in _lines(merged.content):
            if any(_line_matches(line, f) for f in forgotten):
                continue
            if any(_normalize(line) == _normalize(k) for k in kept):
                continue
            kept.append(line)
        content = "\n".join(_trim_front(kept, MAX_LENGTH))
        nickname = state.nickname
        if merged.nickname is not None:
            cleaned = clean_nickname(merged.nickname)
            if cleaned:
                nickname = cleaned
        return content, nickname

    async def _drop_pending(self, pending: PendingSummary) -> None:
        # due_at이 그대로일 때만 지운다 — 요약 중에 새 메시지가 와서 시각이 미뤄졌으면 남긴다.
        await self._conn.execute(
            "DELETE FROM player_memory_pending WHERE player_id = ? AND due_at = ?",
            (pending.player_id, pending.due_at),
        )
        await self._conn.commit()

    async def _retry_or_drop(self, pending: PendingSummary, *, now: float) -> None:
        if pending.attempts + 1 >= MAX_SUMMARY_ATTEMPTS:
            logger.warning("플레이어 %s의 메모리 요약을 %d번 실패해 버린다", pending.player_id, MAX_SUMMARY_ATTEMPTS)
            await self._drop_pending(pending)
            return
        await self._conn.execute(
            "UPDATE player_memory_pending SET due_at = ?, attempts = attempts + 1 "
            "WHERE player_id = ? AND due_at = ?",
            (now + SUMMARY_DELAY_SECONDS, pending.player_id, pending.due_at),
        )
        await self._conn.commit()


def _decode_messages(raw: str) -> list[PendingMessage]:
    try:
        items = json.loads(raw)
        return [PendingMessage(id=str(i["id"]), text=str(i["text"])) for i in items]
    except (ValueError, KeyError, TypeError):
        return []
