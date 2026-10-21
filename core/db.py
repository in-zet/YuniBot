# core/db.py
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from pathlib import Path
from typing import Any

import aiosqlite


class GuardedConnection(aiosqlite.Connection):
    """모든 요청이 같이 쓰는 SQLite 연결. 연결이 하나라 한 요청의 `commit()`·`rollback()`이 다른 요청의
    아직 끝나지 않은 쓰기까지 함께 커밋하거나 지운다. 여러 문장을 한 단위로 써야 하는 쪽은
    `transaction()` 안에서 쓴다 — 그동안 다른 작업의 모든 문장(읽기 포함)은 끝날 때까지 기다린다.

    안에서 다시 `transaction()`을 부르거나 다른 작업(태스크)을 만들어 그 결과를 기다리면 교착되니 하지 않는다."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tx_lock = asyncio.Lock()
        self._tx_owner: asyncio.Task[Any] | None = None

    async def _execute(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        # aiosqlite의 모든 문장·커밋·커서 읽기가 이 길을 지난다.
        while self._tx_owner is not None and self._tx_owner is not asyncio.current_task():
            async with self._tx_lock:
                pass
        return await super()._execute(fn, *args, **kwargs)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._tx_lock:
            self._tx_owner = asyncio.current_task()
            try:
                yield
            finally:
                self._tx_owner = None


async def connect_guarded(database: str | Path) -> GuardedConnection:
    """`await aiosqlite.connect(path)`처럼 연결을 열어 `GuardedConnection`으로 돌려준다."""

    def connector() -> sqlite3.Connection:
        return sqlite3.connect(str(database))

    conn = GuardedConnection(connector, 64)
    _ = await conn
    return conn


def transaction(conn: aiosqlite.Connection) -> AbstractAsyncContextManager[None]:
    """`GuardedConnection`이면 연결 전체를 잠그고, 아니면(시험의 평범한 연결) 아무것도 하지 않는다."""
    if isinstance(conn, GuardedConnection):
        return conn.transaction()
    return nullcontext()
