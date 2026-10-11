# scripts/clear_global_commands.py
"""이 봇 토큰에 전역(global) 슬래시 명령으로 등록된 채로 남아 있는 옛 봇(예: 중단된
chuni-penguin 기반 봇)의 명령을 지운다.

`core/bot.py`의 `setup_hook`은 `TEST_GUILD_ID`가 있으면 길드 범위로만 동기화하고
전역 명령은 건드리지 않는다. 같은 토큰을 쓰던 예전 봇이 전역으로 등록해 둔 명령은
그래서 코드를 아무리 새로 배포해도 디스코드 쪽에 그대로 남아 명령 목록에 뜨고(실행하면
무응답), 이 스크립트로 한 번 비워야 없어진다. 평소 운영에는 필요 없고, 토큰을 옛 봇과
같이 쓰다가 전역 명령이 남아 있는 걸 발견했을 때만 한 번 돌리면 된다.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from core.config import load_config

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    config = load_config()
    client = discord.Client(intents=discord.Intents.none())
    tree = discord.app_commands.CommandTree(client)

    @client.event
    async def on_ready() -> None:
        try:
            before = await tree.fetch_commands()
            names = [cmd.name for cmd in before]
            print(f"지우기 전 전역 명령 {len(names)}개: {names}")

            tree.clear_commands(guild=None)
            await tree.sync()

            after = await tree.fetch_commands()
            print(f"지운 뒤 전역 명령 {len(after)}개")
        finally:
            await client.close()

    await client.start(config.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
