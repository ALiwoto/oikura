from __future__ import annotations

from io import BytesIO
import logging
from typing import Any, Optional

from pixivpy3 import AppPixivAPI
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import InputMediaDocument, InputMediaPhoto, Message
from trd_utils.html_utils import html_link, html_normal

from oikura.client import OikuraBot
from oikura.utils.pixiv import PixivIllustInfo, do_pixiv_auth
from oikura.utils.urls import get_pixiv_illust_id


logger = logging.getLogger(__name__)


def _get_chat_username(message: Message) -> str:
    if message.chat.username:
        return message.chat.username

    usernames = getattr(message.chat, "usernames", None) or []
    if usernames:
        return getattr(usernames[0], "username", "") or ""

    return ""


def _build_pixiv_caption(message: Message, url: str) -> str:
    caption = html_link("Artist", url)
    chat_username = _get_chat_username(message)
    if chat_username:
        caption += "\n@" + html_normal(chat_username)

    return caption[:1024]


def _download_pixiv_file(
    pixiv_api: AppPixivAPI,
    url: str,
    file_name: str,
) -> BytesIO:
    output = BytesIO()
    output.name = file_name
    pixiv_api.download(url, fname=output)
    output.seek(0)
    return output


async def handle_pixiv(client: OikuraBot, message: Message, url: str) -> None:
    pixiv_api = client.pixiv_api
    if not isinstance(pixiv_api, AppPixivAPI):
        return

    illust_id = get_pixiv_illust_id(url)
    if not illust_id:
        return

    do_pixiv_auth(
        pixiv_api,
        access_token=client.config.pixiv.access_token,
        refresh_token=client.config.pixiv.refresh_token,
    )

    illust = pixiv_api.illust_detail(illust_id)
    if not illust:
        return

    illust_info = PixivIllustInfo(illust)
    if illust_info.has_error:
        do_pixiv_auth(
            pixiv_api,
            access_token=client.config.pixiv.access_token,
            refresh_token=client.config.pixiv.refresh_token,
        )
        illust = pixiv_api.illust_detail(illust_id)
        if not illust:
            return

        illust_info = PixivIllustInfo(illust)
        if illust_info.has_error:
            logger.warning("failed to get pixiv illust %s: %s", illust_id, illust)
            return

    caption = _build_pixiv_caption(message, url)
    reply_to_message_id = message.id if message.chat.type != ChatType.CHANNEL else None

    if illust_info.is_multiple:
        await _send_multiple_pixiv_pages(
            client=client,
            message=message,
            pixiv_api=pixiv_api,
            illust_info=illust_info,
            illust_id=illust_id,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )
        return

    await _send_single_pixiv_page(
        client=client,
        message=message,
        pixiv_api=pixiv_api,
        illust=illust,
        illust_id=illust_id,
        caption=caption,
        reply_to_message_id=reply_to_message_id,
    )


async def _send_multiple_pixiv_pages(
    *,
    client: OikuraBot,
    message: Message,
    pixiv_api: AppPixivAPI,
    illust_info: PixivIllustInfo,
    illust_id: str,
    caption: str,
    reply_to_message_id: Optional[int],
) -> None:
    preview_inputs: list[InputMediaPhoto] = []
    original_inputs: list[InputMediaDocument] = []

    for index, current_meta in enumerate(illust_info.meta_pages[:10], start=1):
        large_url = current_meta.image_urls.large
        original_url = current_meta.image_urls.original

        preview_inputs.append(
            InputMediaPhoto(
                media=_download_pixiv_file(
                    pixiv_api,
                    large_url,
                    f"pic_{illust_id}_{index}.jpg",
                ),
                caption=caption if index == 1 else "",
                parse_mode=ParseMode.HTML,
            )
        )
        original_inputs.append(
            InputMediaDocument(
                media=_download_pixiv_file(
                    pixiv_api,
                    original_url,
                    original_url.rsplit("/", maxsplit=1)[-1],
                )
            )
        )

    if not preview_inputs:
        return

    sent_album = await client.send_media_group(
        chat_id=message.chat.id,
        media=preview_inputs,
        reply_to_message_id=reply_to_message_id,
    )
    if original_inputs:
        await client.send_media_group(
            chat_id=message.chat.id,
            media=original_inputs,
            reply_to_message_id=sent_album[0].message_id,
        )


async def _send_single_pixiv_page(
    *,
    client: OikuraBot,
    message: Message,
    pixiv_api: AppPixivAPI,
    illust: Any,
    illust_id: str,
    caption: str,
    reply_to_message_id: Optional[int],
) -> None:
    large_url = illust.illust.image_urls.large
    preview = _download_pixiv_file(pixiv_api, large_url, f"pic_{illust_id}.jpg")

    try:
        sent_photo_msg = await client.send_photo(
            chat_id=message.chat.id,
            photo=preview,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception:
        medium_url = illust.illust.image_urls.medium
        preview = _download_pixiv_file(
            pixiv_api,
            medium_url,
            f"pic_medium_{illust_id}.jpg",
        )
        sent_photo_msg = await client.send_photo(
            chat_id=message.chat.id,
            photo=preview,
            reply_to_message_id=reply_to_message_id,
        )

    try:
        original_url = illust.illust.meta_single_page.original_image_url
        original = _download_pixiv_file(
            pixiv_api,
            original_url,
            original_url.rsplit("/", maxsplit=1)[-1],
        )
        await client.send_document(
            chat_id=message.chat.id,
            document=original,
            reply_to_message_id=sent_photo_msg.message_id,
        )
    except Exception as ex:
        logger.warning(
            "failed to download/send original pixiv image: %s",
            ex,
            stacklevel=3,
        )
