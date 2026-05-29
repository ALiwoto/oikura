from __future__ import annotations

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message
from trd_utils.html_utils import html_bold, html_mono

from oikura.client import OikuraBot, get_bot
from oikura.config import add_whitelisted_chat, remove_whitelisted_chat


bot = get_bot()


def _is_owner(client: OikuraBot, message: Message) -> bool:
    owner_id = client.config.oikura.owner_id
    return bool(owner_id and message.from_user and message.from_user.id == owner_id)


def _command_arg(message: Message) -> str:
    parts = (message.text or message.caption or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


@bot.on_message(filters.command("whitelist", prefixes="/"))
async def whitelist_chat(client: OikuraBot, message: Message) -> None:
    if not _is_owner(client, message):
        return

    chat_id = _command_arg(message)
    if not chat_id:
        await message.reply_text(
            html_bold("Usage: ") + html_mono("/whitelist <chat_id>"),
            parse_mode=ParseMode.HTML,
        )
        return

    normalized = add_whitelisted_chat(client.config, chat_id)
    await message.reply_text(
        html_bold("Whitelisted: ") + html_mono(str(normalized)),
        parse_mode=ParseMode.HTML,
    )


@bot.on_message(filters.command("rmwhitelist", prefixes="/"))
async def remove_whitelist_chat(client: OikuraBot, message: Message) -> None:
    if not _is_owner(client, message):
        return

    chat_id = _command_arg(message)
    if not chat_id:
        await message.reply_text(
            html_bold("Usage: ") + html_mono("/rmwhitelist <chat_id>"),
            parse_mode=ParseMode.HTML,
        )
        return

    normalized = remove_whitelisted_chat(client.config, chat_id)
    await message.reply_text(
        html_bold("Removed: ") + html_mono(str(normalized)),
        parse_mode=ParseMode.HTML,
    )
