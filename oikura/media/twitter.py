from __future__ import annotations

import shutil
import tempfile
from typing import Any, Optional

from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import (
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from oikura.client import OikuraBot
from oikura.utils.twitter_caption import build_twitter_caption
from oikura.utils.twitter_extractor import extract_twitter_post_data
from oikura.utils.twitter_media import (
    download_twitter_media,
    get_twitter_file_name,
    get_twitter_media_kind,
    get_twitter_video_upload_args,
)
from oikura.utils.twitter_screenshot import render_twitter_text_screenshot
from oikura.utils.urls import has_file_macro


def _build_twitter_album_item(
    media_file: str,
    media_kind: str,
    caption: str,
    video_args: Optional[dict[str, Any]] = None,
):
    if media_kind == "video":
        video_args = video_args or {}
        return InputMediaVideo(
            media=media_file,
            thumb=video_args.get("thumb"),
            caption=caption,
            parse_mode=ParseMode.HTML,
            width=video_args.get("width", 0),
            height=video_args.get("height", 0),
            supports_streaming=True,
        )

    if media_kind == "document":
        return InputMediaDocument(
            media=media_file,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

    return InputMediaPhoto(
        media=media_file,
        caption=caption,
        parse_mode=ParseMode.HTML,
    )


def _should_send_twitter_document_copy(message: Message, media_kind: str) -> bool:
    if has_file_macro(message):
        return True

    if message.chat.type != ChatType.CHANNEL:
        return False

    return media_kind == "photo"


async def _send_twitter_media(
    client: OikuraBot,
    chat_id: int,
    media_file: str,
    media_kind: str,
    caption: str,
    reply_to_message_id: Optional[int] = None,
    media_info: Optional[dict[str, Any]] = None,
    temp_dir: Optional[str] = None,
) -> Message:
    common_args = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": ParseMode.HTML,
        "reply_to_message_id": reply_to_message_id,
    }

    if media_kind == "video":
        video_args = await get_twitter_video_upload_args(
            media_path=media_file,
            temp_dir=temp_dir or tempfile.gettempdir(),
            ffmpeg_path=client.ffmpeg_path,
            media_info=media_info,
        )
        return await client.send_video(
            video=media_file,
            supports_streaming=True,
            width=video_args["width"],
            height=video_args["height"],
            thumb=video_args["thumb"],
            **common_args,
        )

    if media_kind == "document":
        return await client.send_document(
            document=media_file,
            **common_args,
        )

    return await client.send_photo(
        photo=media_file,
        **common_args,
    )


async def handle_twitter(client: OikuraBot, message: Message, url: str) -> None:
    if message.from_user and message.from_user.is_bot:
        return

    if client.http is None:
        return

    twitter_post = extract_twitter_post_data(url)
    if not twitter_post:
        return

    caption = build_twitter_caption(
        url=url,
        post_text=twitter_post.text,
        message=message,
        include_origin_chat=True,
        tweet_info=twitter_post.info,
    )
    reply_to_message_id = message.id if message.chat.type != ChatType.CHANNEL else None
    twitter_temp_dir = tempfile.mkdtemp(prefix="oikura_tw_")

    try:
        if not twitter_post.media:
            if not twitter_post.text:
                return

            screenshot_path = render_twitter_text_screenshot(
                tweet_text=twitter_post.text,
                temp_dir=twitter_temp_dir,
                tweet_identifier=twitter_post.identifier,
                tweet_info=twitter_post.info,
                quoted_post=twitter_post.quoted_post,
            )
            sent_message = await client.send_photo(
                chat_id=message.chat.id,
                photo=screenshot_path,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=reply_to_message_id,
            )
            if has_file_macro(message):
                await client.send_document(
                    chat_id=message.chat.id,
                    document=screenshot_path,
                    reply_to_message_id=sent_message.message_id,
                )
            return

        if len(twitter_post.media) == 1:
            single_media = twitter_post.media[0]
            media_kind = get_twitter_media_kind(
                media_url=single_media.url,
                media_info=single_media.info,
            )
            media_file = await download_twitter_media(
                http_client=client.http,
                url=single_media.url,
                file_name=get_twitter_file_name(
                    tweet_identifier=twitter_post.identifier,
                    media_url=single_media.url,
                    media_kind=media_kind,
                    media_info=single_media.info,
                ),
                temp_dir=twitter_temp_dir,
            )

            sent_message = await _send_twitter_media(
                client=client,
                chat_id=message.chat.id,
                media_file=media_file,
                media_kind=media_kind,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
                media_info=single_media.info,
                temp_dir=twitter_temp_dir,
            )
            if _should_send_twitter_document_copy(message, media_kind):
                await client.send_document(
                    chat_id=message.chat.id,
                    document=media_file,
                    reply_to_message_id=sent_message.message_id,
                )
            return

        album_inputs = []
        document_inputs: list[InputMediaDocument] = []
        for index, current_media in enumerate(twitter_post.media[:10], start=1):
            media_kind = get_twitter_media_kind(
                media_url=current_media.url,
                media_info=current_media.info,
            )
            media_file = await download_twitter_media(
                http_client=client.http,
                url=current_media.url,
                file_name=get_twitter_file_name(
                    tweet_identifier=twitter_post.identifier,
                    media_url=current_media.url,
                    media_kind=media_kind,
                    counter=index,
                    media_info=current_media.info,
                ),
                temp_dir=twitter_temp_dir,
            )
            video_args = None
            if media_kind == "video":
                video_args = await get_twitter_video_upload_args(
                    media_path=media_file,
                    temp_dir=twitter_temp_dir,
                    ffmpeg_path=client.ffmpeg_path,
                    media_info=current_media.info,
                )

            album_inputs.append(
                _build_twitter_album_item(
                    media_file=media_file,
                    media_kind=media_kind,
                    caption=caption if index == 1 else "",
                    video_args=video_args,
                )
            )

            if _should_send_twitter_document_copy(message, media_kind):
                document_inputs.append(InputMediaDocument(media=media_file))

        sent_album = await client.send_media_group(
            chat_id=message.chat.id,
            media=album_inputs,
            reply_to_message_id=reply_to_message_id,
        )

        if not document_inputs:
            return

        if len(document_inputs) == 1:
            await client.send_document(
                chat_id=message.chat.id,
                document=document_inputs[0].media,
                reply_to_message_id=sent_album[0].message_id,
            )
            return

        await client.send_media_group(
            chat_id=message.chat.id,
            media=document_inputs,
            reply_to_message_id=sent_album[0].message_id,
        )
    finally:
        shutil.rmtree(twitter_temp_dir, ignore_errors=True)
