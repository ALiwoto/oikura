from __future__ import annotations

import argparse
import asyncio
import logging
import os

from pyrogram import idle

from oikura.client import init_bot
from oikura.config import load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m oikura")
    parser.add_argument(
        "-c",
        "--config",
        default=os.environ.get("OIKURA_CONFIG"),
        help="Path to the INI config file. Defaults to config.ini.",
    )
    return parser.parse_args()


async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    args = parse_args()
    config = load_config(args.config)
    bot = init_bot(config=config)

    import oikura.handlers.commands  # noqa: F401
    import oikura.handlers.media  # noqa: F401

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
