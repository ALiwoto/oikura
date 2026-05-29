from __future__ import annotations

from pathlib import Path

import aiohttp
from pixivpy3 import AppPixivAPI
from pyrogram import Client

from oikura.config import AppConfig


class OikuraBot(Client):
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.http: aiohttp.ClientSession | None = None
        self.pixiv_api = AppPixivAPI()

        super().__init__(
            name="oikura_bot",
            api_id=config.pyrogram.api_id,
            api_hash=config.pyrogram.api_hash,
            bot_token=config.pyrogram.bot_token,
            workdir=Path("."),
            workers=16,
            no_updates=False,
        )

    @property
    def ffmpeg_path(self) -> str:
        return self.config.oikura.ffmpeg

    async def start(self):
        if self.http is None or self.http.closed:
            self.http = aiohttp.ClientSession()

        return await super().start()

    async def stop(self, block: bool = True):
        try:
            return await super().stop(block=block)
        finally:
            if self.http and not self.http.closed:
                await self.http.close()
