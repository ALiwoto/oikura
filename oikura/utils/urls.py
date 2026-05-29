from __future__ import annotations

from typing import Iterable
from urllib.parse import parse_qs, urlparse

from pyrogram.types import Message


TRAILING_URL_CHARS = ".,;:!?)>]}'\""
LEADING_URL_CHARS = "([<{\"'"


def clean_url(value: str) -> str:
    return (value or "").strip().strip(LEADING_URL_CHARS).rstrip(TRAILING_URL_CHARS)


def _message_text(message: Message) -> str:
    return message.text or message.caption or ""


def get_media_link(message: Message, filters: str | Iterable[str] = "http") -> str | None:
    if isinstance(filters, str):
        filters = (filters,)

    lowered_filters = tuple(item.lower() for item in filters)
    text = _message_text(message)
    for current_part in text.strip().split():
        clean_part = clean_url(current_part)
        lowered_part = clean_part.lower()
        if any(item in lowered_part for item in lowered_filters):
            return clean_part

    entities = message.entities or message.caption_entities or []
    for entity in entities:
        entity_url = getattr(entity, "url", None)
        if entity_url:
            clean_part = clean_url(entity_url)
            lowered_part = clean_part.lower()
            if any(item in lowered_part for item in lowered_filters):
                return clean_part

        offset = getattr(entity, "offset", None)
        length = getattr(entity, "length", None)
        if offset is None or length is None:
            continue

        clean_part = clean_url(text[offset : offset + length])
        lowered_part = clean_part.lower()
        if any(item in lowered_part for item in lowered_filters):
            return clean_part

    return None


def get_pixiv_illust_id(url: str) -> str:
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)

    try:
        illust_id = query_params["illust_id"][0]
        if illust_id:
            return illust_id
    except (KeyError, IndexError):
        pass

    path_parts = [part for part in parsed_url.path.split("/") if part]
    if not path_parts:
        return ""

    return path_parts[-1]


def has_file_macro(message: Message) -> bool:
    return ".file" in _message_text(message).split()
