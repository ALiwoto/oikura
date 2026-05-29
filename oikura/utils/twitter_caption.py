import datetime
from typing import Any, Dict, Optional

from pyrogram.enums import ChatType
from pyrogram.types import Message
from trd_utils.html_utils import html_link, html_normal


BOT_CAPTION_LIMIT = 1024


def get_chat_username(message: Message) -> str:
    if message.chat.username:
        return message.chat.username

    usernames = getattr(message.chat, "usernames", None) or []
    if usernames:
        return getattr(usernames[0], "username", "") or ""

    return ""


def truncate_twitter_text(text: str, max_length: int) -> str:
    text = (text or "").strip()
    if not text or max_length <= 0:
        return ""

    if len(text) <= max_length:
        return text

    if max_length <= 3:
        return ""

    return text[: max_length - 3].rstrip() + "..."


def parse_twitter_post_date(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        return value

    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None

    for parser in (datetime.datetime.fromisoformat,):
        try:
            return parser(value)
        except ValueError:
            continue

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


def humanize_twitter_post_age(post_date: Any) -> str:
    parsed_date = parse_twitter_post_date(post_date)
    if not parsed_date:
        return ""

    now = (
        datetime.datetime.now(tz=parsed_date.tzinfo)
        if parsed_date.tzinfo
        else datetime.datetime.utcnow()
    )
    total_seconds = max(0, int((now - parsed_date).total_seconds()))

    units = (
        ("year", 365 * 24 * 60 * 60),
        ("month", 30 * 24 * 60 * 60),
        ("week", 7 * 24 * 60 * 60),
        ("day", 24 * 60 * 60),
        ("hour", 60 * 60),
        ("minute", 60),
        ("second", 1),
    )
    for unit_name, unit_seconds in units:
        if total_seconds >= unit_seconds:
            value = total_seconds // unit_seconds
            suffix = "" if value == 1 else "s"
            return f"{value} {unit_name}{suffix} ago"

    return "0 seconds ago"


def get_twitter_author_identity(
    tweet_info: Optional[Dict[str, Any]],
) -> tuple[str, str, str]:
    tweet_info = tweet_info or {}
    author_info = tweet_info.get("author")
    if not isinstance(author_info, dict):
        author_info = tweet_info.get("user")
    if not isinstance(author_info, dict):
        return "", "", ""

    # gallery-dl uses "name" for the screen_name/handle and "nick" for
    # the display name, which is the opposite of what these keys suggest.
    author_nick = str(author_info.get("name") or "").strip().lstrip("@")
    author_name = str(author_info.get("nick") or author_nick or "").strip()
    author_id = str(author_info.get("id") or "").strip()

    if author_nick:
        return author_name, author_nick, f"https://x.com/{author_nick}"
    if author_id:
        return author_name, "", f"https://x.com/i/user/{author_id}"
    return author_name, "", ""


def get_twitter_author_info(tweet_info: Optional[Dict[str, Any]]) -> tuple[str, str]:
    author_name, _, author_link = get_twitter_author_identity(tweet_info)
    return author_name, author_link


def build_twitter_caption(
    url: str,
    post_text: str,
    message: Message,
    include_origin_chat: bool,
    tweet_info: Optional[Dict[str, Any]] = None,
) -> str:
    if message.chat.type != ChatType.CHANNEL:
        footer_visible = "Posted"
        footer_html = html_link("Posted", url)
        author_name, author_link = get_twitter_author_info(tweet_info)
        relative_time = humanize_twitter_post_age((tweet_info or {}).get("date"))

        if author_name:
            footer_visible += f" by {author_name}"
            footer_html += " by " + (
                html_link(author_name, author_link)
                if author_link
                else html_normal(author_name)
            )
        if relative_time:
            footer_visible += f", {relative_time}"
            footer_html += ", " + html_normal(relative_time)
    else:
        footer_visible = "Post"
        footer_html = html_link("Post", url)
        chat_username = get_chat_username(message) if include_origin_chat else ""
        if chat_username:
            footer_visible += "\n@" + chat_username
            footer_html += "\n@" + html_normal(chat_username)

    if len(footer_visible) > BOT_CAPTION_LIMIT:
        return html_link("Post", url)

    available_text_len = BOT_CAPTION_LIMIT - len(footer_visible)
    if post_text:
        available_text_len -= 2

    truncated_text = truncate_twitter_text(post_text, available_text_len)
    if truncated_text:
        return html_normal(truncated_text) + "\n\n" + footer_html

    return footer_html
