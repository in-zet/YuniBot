from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    owner TEXT NOT NULL,
    filename TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (owner, filename)
);
"""


class MigrationError(RuntimeError):
    pass


async def run_migrations(conn: aiosqlite.Connection, *, roots: list[tuple[str, Path]]) -> list[str]:
    """roots는 호출자가 이미 적용 순서(코어 먼저, 스킬은 이름순)로 정렬해 넘긴다."""
    await conn.execute(_BOOTSTRAP_SQL)
    await conn.commit()

    applied: list[str] = []
    for owner, root in roots:
        if not root.is_dir():
            continue
        for sql_file in sorted(root.glob("*.sql")):
            applied_name = await _apply_one(conn, owner=owner, sql_file=sql_file)
            if applied_name:
                applied.append(applied_name)
    return applied


async def _apply_one(conn: aiosqlite.Connection, *, owner: str, sql_file: Path) -> str | None:
    content = sql_file.read_text()
    checksum = hashlib.sha256(content.encode()).hexdigest()

    cursor = await conn.execute(
        "SELECT checksum FROM schema_migrations WHERE owner = ? AND filename = ?",
        (owner, sql_file.name),
    )
    row = await cursor.fetchone()
    if row is not None:
        if row[0] != checksum:
            raise MigrationError(
                f"이미 적용한 마이그레이션 파일이 바뀌었다: {owner}/{sql_file.name}"
            )
        return None

    try:
        await conn.execute("BEGIN")
        for statement in _split_statements(content):
            await conn.execute(statement)
        await conn.execute(
            "INSERT INTO schema_migrations (owner, filename, checksum, applied_at) VALUES (?, ?, ?, ?)",
            (owner, sql_file.name, checksum, datetime.now(UTC).isoformat()),
        )
        await conn.commit()
    except Exception as exc:
        await conn.rollback()
        raise MigrationError(f"마이그레이션 실패: {owner}/{sql_file.name}: {exc}") from exc

    return f"{owner}/{sql_file.name}"


def _split_statements(script: str) -> list[str]:
    """SQL 스크립트를 개별 문장으로 나눈다.

    ``sqlite3.Connection.executescript``는 내부적으로 대기 중인 트랜잭션을
    먼저 커밋하고 각 문장을 자동커밋 모드로 실행하기 때문에, DDL 실패 시
    ``rollback()``으로 앞선 문장을 되돌릴 수 없다. 대신 문장을 직접 나눠
    명시적 ``BEGIN``/커밋 안에서 하나씩 실행해 전체를 하나의 트랜잭션으로
    묶는다. 문자열 리터럴('...', 쌍따옴표 식별자, -- 줄 주석, /* */ 블록
    주석) 안의 세미콜론은 문장 구분자로 보지 않는다.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(script)
    while i < n:
        ch = script[i]
        if ch == "'" or ch == '"':
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(script[i])
                if script[i] == quote:
                    if i + 1 < n and script[i + 1] == quote:
                        buf.append(script[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            continue
        if ch == "-" and script[i : i + 2] == "--":
            end = script.find("\n", i)
            end = n if end == -1 else end
            buf.append(script[i:end])
            i = end
            continue
        if ch == "/" and script[i : i + 2] == "/*":
            end = script.find("*/", i + 2)
            end = n if end == -1 else end + 2
            buf.append(script[i:end])
            i = end
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
