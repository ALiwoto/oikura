from .bot import OikuraBot
from oikura.config import AppConfig

bot: OikuraBot | None = None


def init_bot(config: AppConfig) -> OikuraBot:
    global bot
    bot = OikuraBot(config=config)
    return bot


def get_bot() -> OikuraBot:
    if bot is None:
        raise RuntimeError("oikura bot was not initialized before importing handlers")

    return bot


__all__ = ("OikuraBot", "bot", "get_bot", "init_bot")
