import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import pytest


@pytest.fixture(autouse=True)
def _connections_read_rows_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """운영은 `core.db.connect_guarded`가 연결을 열 때 `row_factory`를 정한다. 시험이 `aiosqlite.connect`로
    직접 여는 연결도 같게 해서, 서비스와 스킬 모델이 열 이름으로 읽는 것에 기대도 되게 한다."""

    def connect(database: str | Path, *, iter_chunk_size: int = 64, **kwargs: Any) -> aiosqlite.Connection:
        def connector() -> sqlite3.Connection:
            conn = sqlite3.connect(str(database), **kwargs)
            conn.row_factory = sqlite3.Row
            return conn

        return aiosqlite.Connection(connector, iter_chunk_size)

    monkeypatch.setattr(aiosqlite, "connect", connect)
