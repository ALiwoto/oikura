from __future__ import annotations

from pyrogram import filters
from pyrogram.types import Message

from oikura.client import OikuraBot, get_bot
from oikura.media.pixiv import handle_pixiv
from oikura.media.twitter import handle_twitter
from oikura.utils.urls import get_media_link


bot = get_bot()


@bot.on_message(filters.all)
async def media_router(client: OikuraBot, message: Message) -> None:
    if not _is_whitelisted_chat(client, message):
        return

    twitter_url = get_media_link(message, ("twitter", "x.com"))
    if twitter_url:
        await handle_twitter(client, message, twitter_url)
        return

    pixiv_url = get_media_link(message, "pixiv.net")
    if pixiv_url:
        await handle_pixiv(client, message, pixiv_url)


def _is_whitelisted_chat(client: OikuraBot, message: Message) -> bool:
    chat = getattr(message, "chat", None)
    if not chat:
        return False

    whitelist = client.config.oikura.whitelisted_chats
    if chat.id in whitelist or str(chat.id) in whitelist:
        return True

    username = (getattr(chat, "username", "") or "").lower()
    return bool(username and username in whitelist)
