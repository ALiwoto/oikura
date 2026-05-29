from __future__ import annotations

import argparse
import asyncio
import logging
import os

from pyrogram import idle

from oikura.client import OikuraBot
from oikura.config import load_config
from oikura.handlers import register_media_handlers


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m oikura")
    parser.add_argument(
        "-c",
        "--config",
        default=os.environ.get("OIKURA_CONFIG"),
        help="Path to the INI config file. Defaults to config/config.ini, then config.ini.",
    )
    return parser.parse_args()


async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    args = parse_args()
    config = load_config(args.config)
    bot = OikuraBot(config=config)
    register_media_handlers(bot)

    await bot.start()
    logging.info("oikura bot started with config %s", config.path)
    try:
        await idle()
    finally:
        await bot.stop()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
