# core/message_split.py
from __future__ import annotations

# 디스코드 메시지 본문 한도. 넘기면 400(50035)으로 아예 안 나간다.
DISCORD_MESSAGE_LIMIT = 2000


def split_message(text: str, *, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """한도를 넘는 글을 줄 경계에서 나눈다. 한 줄이 한도보다 길면 그 줄만 글자 수로 자른다.
    한도 안이면 그대로 한 조각이다."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
