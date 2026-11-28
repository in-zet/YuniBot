"""운영 멘션 재현: DB 복사본 + 운영 `build_client` + 디스코드의 실제 대화 기록으로 `MentionEntry.handle`을 다시 돌린다.

평가(`eval_agent.py`)는 질문 하나만 넣어 운영의 대화 맥락(최근 대화 전체·답장 사슬로 이어 받은 스킬·서버 설정)을 못 만든다.
운영에서만 나는 실패는 이걸로 재현한다(2026-11-28: 펼친 스킬이 상한을 넘어 요청 도중 report가 접힌 사고를 이걸로 찾았다).

- DB는 복사본으로만 쓴다(원본을 안 건드린다). 사고 직전 상태면 배포 때 만든 `data/yuni.db.bak-*`가 좋다.
- 대화 기록은 실행할 때마다 디스코드 API(GET, 봇 토큰)로 읽고 `--out` 폴더에만 남긴다 — 저장소에 넣지 않는다.
- NET에 요청을 보내거나 계정을 바꾸는 도구(`--block`)는 실행하지 않고 오류로 돌려준다.
- `--out`에 시도마다 LLM 입력(시스템 프롬프트·대화·도구 목록)·응답·도구 호출·보낸 답을 JSON으로 적는다.

쓰기(저장소 루트에서):
  PYTHONPATH=.:third_party uv run python3 scripts/replay_mention.py \
      --db data/yuni.db.bak-20261128-triggers --channel <채널 id> --guild <서버 id> --out temp/replay \
      --repeat 3 <메시지 id> [<메시지 id> ...]
메시지 id를 여럿 주면 한 시도 안에서 차례로 처리한다(같은 프로세스라 앞 요청의 표·펼친 스킬이 이어진다).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import json
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from core.bot import build_client
from core.config import load_config
from core.mention import ChannelMessage
from core.tool_types import ToolResult

_API = "https://discord.com/api/v10"
_SNOWFLAKE_EPOCH_MS = 1_420_070_400_000
_DEFAULT_BLOCK = ("sync_run", "logout", "login")


def _when(message_id: str) -> datetime.datetime:
    """디스코드 메시지 id에 든 보낸 시각(운영과 같은 서버 로컬 시각)."""
    ms = (int(message_id) >> 22) + _SNOWFLAKE_EPOCH_MS
    return datetime.datetime.fromtimestamp(ms / 1000)  # noqa: DTZ006


async def _fetch(token: str, channel: str, message_ids: list[str]) -> dict[str, Any]:
    """봇 id, 메시지마다 그 메시지와 그 앞 50개(운영 `on_message`와 같은 범위)."""
    async with httpx.AsyncClient(base_url=_API, headers={"Authorization": f"Bot {token}"}, timeout=20) as http:
        bot_id = (await http.get("/users/@me")).raise_for_status().json()["id"]
        messages: dict[str, Any] = {}
        for mid in message_ids:
            trigger = (await http.get(f"/channels/{channel}/messages/{mid}")).raise_for_status().json()
            before = (await http.get(f"/channels/{channel}/messages", params={"before": mid, "limit": 50})).raise_for_status()
            messages[mid] = {"trigger": trigger, "before": before.json()}
    return {"bot_id": bot_id, "messages": messages}


def _to_message(raw: dict[str, Any], *, bot_id: str, channel: str) -> ChannelMessage:
    ref = raw.get("message_reference") or {}
    return ChannelMessage(
        message_id=raw["id"], author_discord_id=raw["author"]["id"], is_bot=bool(raw["author"].get("bot")),
        content=raw["content"], reference_id=ref.get("message_id"),
        mentions_bot=any(m["id"] == bot_id for m in raw.get("mentions", [])), channel_id=channel,
        author_display_name=(raw.get("member") or {}).get("nick") or raw["author"].get("global_name"),
    )


async def _attempt(args: argparse.Namespace, fetched: dict[str, Any], attempt: int) -> list[dict[str, Any]]:
    db = args.out / f"yuni-{attempt}.db"
    shutil.copy(args.db, db)
    client, _registry, _executor = await build_client(replace(load_config(), db_path=db, test_guild_id=None))
    bot_id = fetched["bot_id"]
    entry = client._mention_entry
    entry._bot_discord_id = bot_id
    agent = entry._agent
    log: list[dict[str, Any]] = []
    real_generate = agent._llm.generate
    real_invoke = agent._invoke

    async def generate(**kwargs: Any) -> Any:
        turn = await real_generate(**kwargs)
        log.append({
            "system": kwargs.get("system_instruction"), "contents": [repr(c) for c in kwargs.get("contents", [])],
            "tools": [t.name for t in kwargs.get("tools", [])], "response": repr(turn),
        })
        return turn

    async def invoke(ctx: Any, tool_name: str, call_args: dict[str, Any], **kw: Any) -> ToolResult:
        log.append({"call": tool_name, "args": call_args})
        if any(word in tool_name for word in args.block):
            return ToolResult(error="(재현 중이라 실행하지 않음)")
        return await real_invoke(ctx, tool_name, call_args, **kw)

    agent._llm.generate = generate  # type: ignore[method-assign]
    agent._invoke = invoke  # pyright: ignore[reportAttributeAccessIssue]
    try:
        for mid in args.message_ids:
            raw = fetched["messages"][mid]
            trigger = _to_message(raw["trigger"], bot_id=bot_id, channel=args.channel)
            history = [_to_message(m, bot_id=bot_id, channel=args.channel) for m in reversed(raw["before"])]
            log.append({"trigger": mid, "content": trigger.content})

            async def send(text: str, displays: list[Any]) -> str:
                log.append({"sent": text, "displays": [d.content for d in displays if d.content]})
                return f"replay-{len(log)}"

            await entry.handle(
                trigger=trigger, history=[*history, trigger], guild_id=args.guild, now=_when(mid), send=send,
                typing=contextlib.nullcontext(),  # type: ignore[arg-type]
            )
    finally:
        await client.conn.close()
    return log


def _print(attempt: int, log: list[dict[str, Any]]) -> None:
    print(f"=== 시도 {attempt}")
    for item in log:
        if "trigger" in item:
            print("  >>", item["content"])
        elif "tools" in item:
            opened = Counter(name.split("_")[0] for name in item["tools"] if "_" in name)
            print("    펼친 스킬", sorted(opened))
        elif "call" in item:
            print("    도구", item["call"], json.dumps(item["args"], ensure_ascii=False)[:150])
        elif "sent" in item:
            print("    답:", item["sent"][:300].replace("\n", " "))


async def main(args: argparse.Namespace) -> None:
    args.out.mkdir(parents=True, exist_ok=True)
    fetched = await _fetch(load_config().discord_token, args.channel, args.message_ids)
    (args.out / "discord.json").write_text(json.dumps(fetched, ensure_ascii=False, indent=1))
    for attempt in range(args.repeat):
        log = await _attempt(args, fetched, attempt)
        (args.out / f"run-{attempt}.json").write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str))
        _print(attempt, log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, required=True, help="복사해 쓸 DB(원본은 안 바뀐다)")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--guild", required=True)
    parser.add_argument("--out", type=Path, default=Path("temp/replay"))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--block", nargs="*", default=list(_DEFAULT_BLOCK), help="이 글자가 든 도구는 실행하지 않는다")
    parser.add_argument("message_ids", nargs="+")
    asyncio.run(main(parser.parse_args()))
