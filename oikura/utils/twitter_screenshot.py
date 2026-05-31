import datetime
import logging
import math
import os
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx
from PIL import Image, ImageDraw, ImageFont

from .twitter_caption import get_twitter_author_identity, parse_twitter_post_date
from .twitter_media import build_twitter_temp_path, get_twitter_media_kind


logger = logging.getLogger(__name__)

TWITTER_SCREENSHOT_WIDTH = 820
TWITTER_SCREENSHOT_BORDER = "#2F3336"
TWITTER_SCREENSHOT_BG = "#000000"
TWITTER_SCREENSHOT_PRIMARY = "#E7E9EA"
TWITTER_SCREENSHOT_SECONDARY = "#71767B"
TWITTER_SCREENSHOT_ACCENT = "#1D9BF0"
TWITTER_SCREENSHOT_EXPORT_SCALE = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
FONT_DIR = ASSETS_DIR / "fonts"
ICON_DIR = ASSETS_DIR / "icons"
VERIFIED_BADGE_ICON = ICON_DIR / "twitter_verified.png"

CONTENT_LEFT = 24
CONTENT_RIGHT = 796
CONTENT_WIDTH = CONTENT_RIGHT - CONTENT_LEFT
BODY_TOP = 150
BODY_LINE_HEIGHT = 30
QUOTE_LINE_HEIGHT = 25

IMAGE_RESAMPLE = Image.Resampling.LANCZOS


@dataclass(frozen=True)
class TwitterColorEmojiFont:
    size: int
    source_size: int
    font: ImageFont.ImageFont

    @property
    def scale(self) -> float:
        return self.size / self.source_size


@dataclass(frozen=True)
class TwitterFontStack:
    size: int
    primary: ImageFont.ImageFont
    cjk: Optional[ImageFont.ImageFont] = None
    emoji: Optional[ImageFont.ImageFont | TwitterColorEmojiFont] = None


TextLine = List[tuple[str, str]]


def load_font_from_candidates(size: int, candidate_paths: Sequence[Path | str]):
    for font_path in candidate_paths:
        font_path = str(font_path)
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size=size)
            except OSError:
                continue

    return None


def get_system_emoji_font(size: int):
    candidate_paths: List[str] = []
    if os.name == "nt":
        candidate_paths.extend(
            [
                "C:/Windows/Fonts/seguiemj.ttf",
                "C:/Windows/Fonts/seguisym.ttf",
            ]
        )
    else:
        candidate_paths.extend(
            [
                "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
                "/System/Library/Fonts/Apple Color Emoji.ttc",
            ]
        )

    return load_font_from_candidates(size, candidate_paths)


@lru_cache(maxsize=16)
def get_bundled_color_emoji_font(size: int) -> Optional[TwitterColorEmojiFont]:
    emoji_path = FONT_DIR / "NotoColorEmoji.ttf"
    if not emoji_path.exists():
        return None

    for source_size in (109, 128, 136):
        try:
            return TwitterColorEmojiFont(
                size=size,
                source_size=source_size,
                font=ImageFont.truetype(str(emoji_path), size=source_size),
            )
        except OSError:
            continue
    return None


def load_twitter_screenshot_font(size: int, bold: bool = False) -> TwitterFontStack:
    primary = load_font_from_candidates(
        size,
        [
            FONT_DIR / ("NotoSans-Bold.ttf" if bold else "NotoSans-Regular.ttf"),
            "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf",
        ],
    ) or ImageFont.load_default(size=size)
    cjk = load_font_from_candidates(
        size,
        [
            FONT_DIR
            / ("NotoSansCJKjp-Bold.otf" if bold else "NotoSansCJKjp-Regular.otf"),
        ],
    )
    return TwitterFontStack(
        size=size,
        primary=primary,
        cjk=cjk,
        emoji=get_system_emoji_font(size) or get_bundled_color_emoji_font(size),
    )


def is_cjk_char(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return (
        0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0xFF00 <= codepoint <= 0xFFEF
    )


def is_emoji_char(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or codepoint in {0x200D, 0xFE0E, 0xFE0F}
        or unicodedata.category(char) == "So"
    )


def get_font_for_char(font: TwitterFontStack, char: str):
    if is_emoji_char(char) and font.emoji:
        return font.emoji
    if is_cjk_char(char) and font.cjk:
        return font.cjk
    return font.primary


def iter_text_font_runs(text: str, font: TwitterFontStack):
    current_font = None
    current_text = ""
    for char in text:
        char_font = get_font_for_char(font, char)
        if current_font is not None and char_font is current_font:
            current_text += char
            continue
        if current_text:
            yield current_text, current_font
        current_text = char
        current_font = char_font
    if current_text:
        yield current_text, current_font


def get_font_text_width(font, text: str) -> int:
    if isinstance(font, TwitterColorEmojiFont):
        return int(math.ceil(font.font.getlength(text) * font.scale))
    try:
        return int(math.ceil(font.getlength(text)))
    except AttributeError:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]


def get_twitter_text_width(font, text: str) -> int:
    if isinstance(font, TwitterFontStack):
        return sum(
            get_font_text_width(run_font, run_text)
            for run_text, run_font in iter_text_font_runs(text, font)
        )
    return get_font_text_width(font, text)


def get_twitter_text_bbox(font, text: str) -> tuple[int, int, int, int]:
    width = get_twitter_text_width(font, text)
    if isinstance(font, TwitterFontStack):
        return (0, 0, width, font.size)
    try:
        return font.getbbox(text)
    except AttributeError:
        return (0, 0, width, 0)


def draw_twitter_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font,
    fill: str,
) -> int:
    if not isinstance(font, TwitterFontStack):
        draw.text(xy, text, font=font, fill=fill)
        return get_twitter_text_width(font, text)

    x, y = xy
    cursor_x = x
    for run_text, run_font in iter_text_font_runs(text, font):
        if isinstance(run_font, TwitterColorEmojiFont):
            large_width = max(1, int(math.ceil(run_font.font.getlength(run_text))))
            large_height = run_font.source_size + 28
            layer = Image.new("RGBA", (large_width, large_height), (0, 0, 0, 0))
            layer_draw = ImageDraw.Draw(layer)
            layer_draw.text((0, 0), run_text, font=run_font.font, embedded_color=True)
            target_width = max(1, int(math.ceil(large_width * run_font.scale)))
            target_height = max(1, int(math.ceil(large_height * run_font.scale)))
            emoji_layer = layer.resize((target_width, target_height), IMAGE_RESAMPLE)
            target_image = getattr(draw, "_image", None)
            if target_image is not None:
                target_image.paste(emoji_layer, (int(cursor_x), int(y - 2)), emoji_layer)
            cursor_x += target_width
            continue

        kwargs = {}
        if run_font is font.emoji:
            kwargs["embedded_color"] = True
        try:
            draw.text((cursor_x, y), run_text, font=run_font, fill=fill, **kwargs)
        except Exception:
            draw.text((cursor_x, y), run_text, font=run_font, fill=fill)
        cursor_x += get_font_text_width(run_font, run_text)
    return int(math.ceil(cursor_x - x))


def fit_twitter_text(text: str, font, max_width: int) -> str:
    text = str(text or "")
    if get_twitter_text_width(font, text) <= max_width:
        return text

    suffix = "..."
    while text and get_twitter_text_width(font, text + suffix) > max_width:
        text = text[:-1]
    return (text.rstrip() + suffix) if text else suffix


def get_twitter_avatar_text(tweet_info: Optional[Dict[str, Any]]) -> str:
    author_name, author_handle, _ = get_twitter_author_identity(tweet_info)
    base_text = author_name or author_handle or "X"
    initials = "".join(part[:1] for part in base_text.split()[:2]).upper()
    return initials or base_text[:2].upper() or "X"


def get_author_info(tweet_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    tweet_info = tweet_info or {}
    author_name, author_handle, _ = get_twitter_author_identity(tweet_info)
    author_info = tweet_info.get("author")
    if not isinstance(author_info, dict):
        author_info = tweet_info.get("user")
    if not isinstance(author_info, dict):
        author_info = {}

    return {
        "name": author_name or "X User",
        "handle": author_handle,
        "avatar": str(author_info.get("profile_image") or "").strip(),
        "verified": bool(
            author_info.get("verified") or author_info.get("blue_verified")
        ),
        "subscribe": str(author_info.get("professional_type") or "").lower()
        == "creator",
    }


def get_compact_count(value: Any) -> str:
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return ""

    if count >= 1_000_000:
        text = f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        text = f"{count / 1_000:.1f}K"
    else:
        return str(count) if count > 0 else ""

    return text.replace(".0", "")


def format_twitter_timestamp(value: Any) -> str:
    parsed_date = parse_twitter_post_date(value)
    if not parsed_date:
        return ""

    hour = parsed_date.hour % 12 or 12
    am_pm = "AM" if parsed_date.hour < 12 else "PM"
    month = parsed_date.strftime("%b")
    return f"{hour}:{parsed_date.minute:02d} {am_pm} · {month} {parsed_date.day}, {parsed_date.year}"


def format_x_relative_age(value: Any) -> str:
    parsed_date = parse_twitter_post_date(value)
    if not parsed_date:
        return ""

    now = (
        datetime.datetime.now(tz=parsed_date.tzinfo)
        if parsed_date.tzinfo
        else datetime.datetime.utcnow()
    )
    seconds = max(0, int((now - parsed_date).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 604800:
        return f"{seconds // 86400}d"
    return parsed_date.strftime("%b %d")


def split_twitter_word(word: str, font, max_width: int) -> List[str]:
    if not word:
        return [""]

    chunks: List[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and get_twitter_text_width(font, candidate) > max_width:
            chunks.append(current)
            current = char
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks or [word]


def get_token_color(token: str) -> str:
    stripped = token.strip()
    if stripped.startswith("@") or stripped.startswith("#"):
        return TWITTER_SCREENSHOT_ACCENT
    if stripped.startswith(("http://", "https://", "x.com/", "twitter.com/")):
        return TWITTER_SCREENSHOT_ACCENT
    return TWITTER_SCREENSHOT_PRIMARY


def rich_line_width(line: TextLine, font) -> int:
    return sum(get_twitter_text_width(font, segment[0]) for segment in line)


def append_rich_segment(line: TextLine, text: str, color: str) -> TextLine:
    if not text:
        return line
    if line and line[-1][1] == color:
        line[-1] = (line[-1][0] + text, color)
    else:
        line.append((text, color))
    return line


def wrap_rich_text(text: str, font, max_width: int) -> List[TextLine]:
    paragraphs = (text or "").splitlines() or [""]
    lines: List[TextLine] = []

    for paragraph_index, paragraph in enumerate(paragraphs):
        stripped_paragraph = paragraph.strip()
        if not stripped_paragraph:
            if paragraph_index:
                lines.append([])
            continue

        if paragraph_index and lines and lines[-1]:
            lines.append([])

        current_line: TextLine = []
        for word in stripped_paragraph.split():
            parts = (
                split_twitter_word(word, font=font, max_width=max_width)
                if get_twitter_text_width(font, word) > max_width
                else [word]
            )
            for part in parts:
                prefix = "" if not current_line else " "
                segment_text = prefix + part
                segment_color = get_token_color(part)
                candidate = list(current_line)
                append_rich_segment(candidate, segment_text, segment_color)
                if current_line and rich_line_width(candidate, font) > max_width:
                    lines.append(current_line)
                    current_line = [(part, segment_color)]
                else:
                    current_line = candidate

        if current_line:
            lines.append(current_line)

    return lines or [[]]


def truncate_rich_lines(
    lines: Sequence[TextLine],
    font,
    max_width: int,
    max_text_lines: int,
) -> List[TextLine]:
    output: List[TextLine] = []
    text_line_count = 0
    truncated = False

    for line in lines:
        if not line:
            if output and text_line_count < max_text_lines:
                output.append([])
            continue
        if text_line_count >= max_text_lines:
            truncated = True
            break
        output.append(list(line))
        text_line_count += 1

    if not truncated:
        return output

    for line_index in range(len(output) - 1, -1, -1):
        line = output[line_index]
        if not line:
            continue
        text, color = line[-1]
        text = text.rstrip()
        while text and rich_line_width(line[:-1] + [(text + "...", color)], font) > max_width:
            text = text[:-1].rstrip()
        line[-1] = ((text + "...") if text else "...", color)
        break

    return output


def measure_rich_lines(lines: Sequence[TextLine], line_height: int) -> int:
    if not lines:
        return 0
    return len(lines) * line_height


def draw_rich_lines(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    lines: Sequence[TextLine],
    font,
    line_height: int,
) -> None:
    x, y = xy
    for line in lines:
        cursor_x = x
        if line:
            for text, color in line:
                draw_twitter_text(draw, (cursor_x, y), text, font=font, fill=color)
                cursor_x += get_twitter_text_width(font, text)
        y += line_height


def fetch_twitter_image(url: str) -> Optional[Image.Image]:
    if not url:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    for _ in range(3):
        try:
            response = httpx.get(
                url,
                follow_redirects=True,
                headers=headers,
                timeout=10.0,
            )
            response.raise_for_status()
            return Image.open(BytesIO(response.content)).convert("RGBA")
        except Exception:
            continue
    return None


def cover_resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_width, target_height = size
    image_width, image_height = image.size
    if not image_width or not image_height:
        return Image.new("RGBA", size, "#111111")

    source_ratio = image_width / image_height
    target_ratio = target_width / target_height
    if source_ratio > target_ratio:
        crop_width = int(image_height * target_ratio)
        left = max(0, (image_width - crop_width) // 2)
        image = image.crop((left, 0, left + crop_width, image_height))
    else:
        crop_height = int(image_width / target_ratio)
        top = max(0, (image_height - crop_height) // 2)
        image = image.crop((0, top, image_width, top + crop_height))

    return image.resize(size, IMAGE_RESAMPLE)


def paste_circle_image(
    image: Image.Image,
    source: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    width = box[2] - box[0]
    height = box[3] - box[1]
    avatar = cover_resize(source, (width, height))
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, width - 1, height - 1), fill=255)
    image.paste(avatar, (box[0], box[1]), mask)


def draw_text_center(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font,
    fill: str,
) -> None:
    bbox = get_twitter_text_bbox(font, text)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw_twitter_text(
        draw,
        (
            box[0] + ((box[2] - box[0]) - width) / 2,
            box[1] + ((box[3] - box[1]) - height) / 2 - 1,
        ),
        text,
        font=font,
        fill=fill,
    )


def draw_avatar(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    avatar_image: Optional[Image.Image],
    tweet_info: Optional[Dict[str, Any]],
    font,
) -> None:
    if avatar_image:
        paste_circle_image(image, avatar_image, box)
        return

    draw.ellipse(box, fill="#1F9CF0")
    draw_text_center(draw, box, get_twitter_avatar_text(tweet_info), font, "#FFFFFF")


@lru_cache(maxsize=16)
def load_verified_badge_icon(size: int) -> Optional[Image.Image]:
    if not VERIFIED_BADGE_ICON.exists():
        return None
    try:
        return Image.open(VERIFIED_BADGE_ICON).convert("RGBA").resize(
            (size, size),
            IMAGE_RESAMPLE,
        )
    except Exception:
        return None


def draw_verified_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    size: int = 18,
) -> None:
    badge = load_verified_badge_icon(size)
    if badge:
        target_image = getattr(draw, "_image", None)
        if target_image is not None:
            target_image.paste(badge, (x, y), badge)
            return
        draw.bitmap((x, y), badge.convert("L"), fill=TWITTER_SCREENSHOT_ACCENT)
        return

    draw.ellipse((x, y, x + size, y + size), fill=TWITTER_SCREENSHOT_ACCENT)
    draw.line(
        (
            x + size * 0.30,
            y + size * 0.53,
            x + size * 0.45,
            y + size * 0.68,
            x + size * 0.72,
            y + size * 0.34,
        ),
        fill="#FFFFFF",
        width=max(2, size // 8),
        joint="curve",
    )


def draw_back_arrow(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    color = TWITTER_SCREENSHOT_PRIMARY
    draw.line((x + 16, y, x, y + 11, x + 16, y + 22), fill=color, width=2)
    draw.line((x + 1, y + 11, x + 26, y + 11), fill=color, width=2)


def get_quoted_post_media_image(quoted_post: Any) -> Optional[Image.Image]:
    media_items = getattr(quoted_post, "media", None) or []
    for media in media_items:
        media_url = getattr(media, "url", "")
        media_info = getattr(media, "info", None)
        if get_twitter_media_kind(media_url=media_url, media_info=media_info) != "photo":
            continue
        candidate_urls = [media_url]
        if isinstance(media_info, dict):
            fallback_urls = media_info.get("_fallback")
            if fallback_urls:
                candidate_urls.extend(str(url) for url in fallback_urls)
        for candidate_url in candidate_urls:
            image = fetch_twitter_image(candidate_url)
            if image:
                return image
    return None


def get_quoted_post_info(quoted_post: Any) -> Optional[Dict[str, Any]]:
    info = getattr(quoted_post, "info", None)
    return info if isinstance(info, dict) else None


def get_quoted_post_text(quoted_post: Any) -> str:
    return str(getattr(quoted_post, "text", "") or "").strip()


def calculate_quote_card_height(
    quoted_post: Any,
    quote_font,
    media_image: Optional[Image.Image],
) -> tuple[int, List[TextLine], int]:
    max_text_lines = 4 if media_image else 6
    quote_lines = truncate_rich_lines(
        wrap_rich_text(get_quoted_post_text(quoted_post), quote_font, CONTENT_WIDTH - 30),
        quote_font,
        CONTENT_WIDTH - 30,
        max_text_lines,
    )
    text_height = measure_rich_lines(quote_lines, QUOTE_LINE_HEIGHT)

    media_height = 0
    if media_image:
        media_height = max(
            180,
            int((CONTENT_WIDTH - 2) * media_image.height / max(1, media_image.width)),
        )

    height = 16 + 36 + 14 + text_height + 14
    if media_image:
        height += media_height + 1
    else:
        height += 2
    return height, quote_lines, media_height


def render_quote_card(
    quoted_post: Any,
    avatar_image: Optional[Image.Image],
    media_image: Optional[Image.Image],
    fonts: Dict[str, Any],
) -> Image.Image:
    quote_info = get_quoted_post_info(quoted_post) or {}
    card_height, quote_lines, media_height = calculate_quote_card_height(
        quoted_post,
        fonts["quote"],
        media_image,
    )
    card_width = CONTENT_WIDTH
    radius = 22

    layer = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((0, 0, card_width - 1, card_height - 1), radius=radius, fill=TWITTER_SCREENSHOT_BORDER)
    draw.rounded_rectangle((1, 1, card_width - 2, card_height - 2), radius=radius - 1, fill=TWITTER_SCREENSHOT_BG)

    avatar_box = (15, 16, 47, 48)
    draw_avatar(layer, draw, avatar_box, avatar_image, quote_info, fonts["quote_avatar"])

    author = get_author_info(quote_info)
    name_x = 55
    name_y = 16
    meta_y = 17
    name_text = fit_twitter_text(author["name"], fonts["quote_name"], 170)
    draw_twitter_text(
        draw,
        (name_x, name_y),
        name_text,
        font=fonts["quote_name"],
        fill=TWITTER_SCREENSHOT_PRIMARY,
    )
    cursor_x = name_x + get_twitter_text_width(fonts["quote_name"], name_text) + 5
    if author["verified"]:
        draw_verified_badge(draw, cursor_x, name_y + 4, size=17)
        cursor_x += 22

    meta_parts = []
    if author["handle"]:
        meta_parts.append("@" + author["handle"])
    age = format_x_relative_age(quote_info.get("date"))
    if age:
        meta_parts.append(age)
    if meta_parts:
        draw_twitter_text(
            draw,
            (cursor_x, meta_y),
            fit_twitter_text(" · ".join(meta_parts), fonts["quote_meta"], card_width - cursor_x - 14),
            font=fonts["quote_meta"],
            fill=TWITTER_SCREENSHOT_SECONDARY,
        )

    text_top = 57
    draw_rich_lines(
        draw,
        (15, text_top),
        quote_lines,
        fonts["quote"],
        QUOTE_LINE_HEIGHT,
    )

    if media_image:
        media_top = text_top + measure_rich_lines(quote_lines, QUOTE_LINE_HEIGHT) + 14
        media_width = card_width - 2
        media = cover_resize(media_image, (media_width, media_height))
        rounded_mask = Image.new("L", (card_width, card_height), 0)
        mask_draw = ImageDraw.Draw(rounded_mask)
        mask_draw.rounded_rectangle((1, 1, card_width - 2, card_height - 2), radius=radius - 1, fill=255)
        media_mask = rounded_mask.crop((1, media_top, card_width - 1, media_top + media_height))
        layer.paste(media, (1, media_top), media_mask)
        draw.line((1, media_top, card_width - 2, media_top), fill="#16181C", width=1)

    draw.rounded_rectangle((0, 0, card_width - 1, card_height - 1), radius=radius, outline=TWITTER_SCREENSHOT_BORDER, width=1)
    return layer


def draw_main_author(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    tweet_info: Optional[Dict[str, Any]],
    avatar_image: Optional[Image.Image],
    fonts: Dict[str, Any],
) -> None:
    avatar_box = (CONTENT_LEFT, 75, CONTENT_LEFT + 49, 124)
    draw_avatar(image, draw, avatar_box, avatar_image, tweet_info, fonts["avatar"])

    author = get_author_info(tweet_info)
    name_x = 84
    name_y = 76
    handle_y = 102
    button_box = (
        CONTENT_RIGHT - 123,
        78,
        CONTENT_RIGHT,
        117,
    )
    max_name_width = (
        button_box[0] - name_x - 28
        if author["subscribe"]
        else CONTENT_RIGHT - name_x
    )
    name_text = fit_twitter_text(author["name"], fonts["name"], max_name_width)
    draw_twitter_text(
        draw,
        (name_x, name_y),
        name_text,
        font=fonts["name"],
        fill=TWITTER_SCREENSHOT_PRIMARY,
    )
    cursor_x = name_x + get_twitter_text_width(fonts["name"], name_text) + 6
    if author["verified"]:
        draw_verified_badge(draw, cursor_x, name_y + 6, size=18)

    if author["handle"]:
        draw_twitter_text(
            draw,
            (name_x, handle_y),
            fit_twitter_text("@" + author["handle"], fonts["handle"], 450),
            font=fonts["handle"],
            fill=TWITTER_SCREENSHOT_SECONDARY,
        )

    if author["subscribe"]:
        draw.rounded_rectangle(button_box, radius=20, fill="#EFF3F4")
        draw_text_center(draw, button_box, "Subscribe", fonts["subscribe"], "#0F1419")


def draw_timestamp(
    draw: ImageDraw.ImageDraw,
    tweet_info: Optional[Dict[str, Any]],
    y: int,
    fonts: Dict[str, Any],
) -> None:
    tweet_info = tweet_info or {}
    timestamp = format_twitter_timestamp(tweet_info.get("date"))
    view_count = get_compact_count(tweet_info.get("view_count"))

    if not timestamp and not view_count:
        return

    cursor_x = CONTENT_LEFT
    if timestamp:
        suffix = " · " if view_count else ""
        text = timestamp + suffix
        draw_twitter_text(
            draw,
            (cursor_x, y),
            text,
            font=fonts["timestamp"],
            fill=TWITTER_SCREENSHOT_SECONDARY,
        )
        cursor_x += get_twitter_text_width(fonts["timestamp"], text)
    if view_count:
        draw_twitter_text(
            draw,
            (cursor_x, y),
            view_count,
            font=fonts["timestamp_bold"],
            fill=TWITTER_SCREENSHOT_PRIMARY,
        )
        cursor_x += get_twitter_text_width(fonts["timestamp_bold"], view_count)
        draw_twitter_text(
            draw,
            (cursor_x, y),
            " Views",
            font=fonts["timestamp"],
            fill=TWITTER_SCREENSHOT_SECONDARY,
        )


def upscale_twitter_screenshot(image: Image.Image) -> Image.Image:
    if TWITTER_SCREENSHOT_EXPORT_SCALE <= 1:
        return image

    return image.resize(
        (
            image.width * TWITTER_SCREENSHOT_EXPORT_SCALE,
            image.height * TWITTER_SCREENSHOT_EXPORT_SCALE,
        ),
        IMAGE_RESAMPLE,
    )


def render_twitter_text_screenshot_python(
    tweet_text: str,
    temp_dir: str,
    tweet_identifier: str,
    tweet_info: Optional[Dict[str, Any]] = None,
    quoted_post: Any = None,
) -> str:
    fonts = {
        "title": load_twitter_screenshot_font(25, bold=True),
        "name": load_twitter_screenshot_font(20, bold=True),
        "handle": load_twitter_screenshot_font(19),
        "body": load_twitter_screenshot_font(21),
        "avatar": load_twitter_screenshot_font(18, bold=True),
        "subscribe": load_twitter_screenshot_font(16, bold=True),
        "timestamp": load_twitter_screenshot_font(18),
        "timestamp_bold": load_twitter_screenshot_font(18, bold=True),
        "action": load_twitter_screenshot_font(15),
        "quote_name": load_twitter_screenshot_font(18, bold=True),
        "quote_meta": load_twitter_screenshot_font(18),
        "quote": load_twitter_screenshot_font(19),
        "quote_avatar": load_twitter_screenshot_font(13, bold=True),
    }

    main_author = get_author_info(tweet_info)
    main_avatar = fetch_twitter_image(main_author["avatar"])
    quote_avatar = None
    quote_media = None
    if quoted_post:
        quote_info = get_quoted_post_info(quoted_post) or {}
        quote_author = get_author_info(quote_info)
        quote_avatar = fetch_twitter_image(quote_author["avatar"])
        quote_media = get_quoted_post_media_image(quoted_post)

    body_lines = wrap_rich_text(tweet_text, fonts["body"], CONTENT_WIDTH)
    body_height = measure_rich_lines(body_lines, BODY_LINE_HEIGHT)

    quote_layer = None
    quote_top = 0
    if quoted_post:
        quote_layer = render_quote_card(
            quoted_post=quoted_post,
            avatar_image=quote_avatar,
            media_image=quote_media,
            fonts=fonts,
        )
        quote_top = BODY_TOP + body_height + 7
        timestamp_y = quote_top + quote_layer.height + 17
    else:
        timestamp_y = BODY_TOP + body_height + 19

    canvas_height = timestamp_y + 46

    image = Image.new("RGBA", (TWITTER_SCREENSHOT_WIDTH, canvas_height), TWITTER_SCREENSHOT_BG)
    draw = ImageDraw.Draw(image)

    draw.line((0, 0, 0, canvas_height), fill=TWITTER_SCREENSHOT_BORDER, width=1)
    draw.line((TWITTER_SCREENSHOT_WIDTH - 1, 0, TWITTER_SCREENSHOT_WIDTH - 1, canvas_height), fill=TWITTER_SCREENSHOT_BORDER, width=1)

    draw_back_arrow(draw, 29, 17)
    draw_twitter_text(draw, (94, 13), "Post", font=fonts["title"], fill=TWITTER_SCREENSHOT_PRIMARY)

    draw_main_author(image, draw, tweet_info, main_avatar, fonts)
    draw_rich_lines(draw, (CONTENT_LEFT, BODY_TOP), body_lines, fonts["body"], BODY_LINE_HEIGHT)

    if quote_layer:
        image.alpha_composite(quote_layer, (CONTENT_LEFT, quote_top))

    draw_timestamp(draw, tweet_info, timestamp_y, fonts)

    output_path = build_twitter_temp_path(
        temp_dir=temp_dir,
        file_name=f"{tweet_identifier}_text.png",
    )
    image = upscale_twitter_screenshot(image)
    image.convert("RGB").save(output_path, format="PNG", optimize=True)
    return output_path


def render_twitter_text_screenshot(
    tweet_text: str,
    temp_dir: str,
    tweet_identifier: str,
    tweet_info: Optional[Dict[str, Any]] = None,
    quoted_post: Any = None,
) -> str:
    try:
        from .twitter_rust_screenshot import (
            TwitterScreenshotRendererError,
            has_twitter_screenshot_renderer_bin,
            render_twitter_text_screenshot as render_twitter_text_screenshot_rust,
        )

        if has_twitter_screenshot_renderer_bin():
            try:
                return render_twitter_text_screenshot_rust(
                    tweet_text=tweet_text,
                    temp_dir=temp_dir,
                    tweet_identifier=tweet_identifier,
                    tweet_info=tweet_info,
                    quoted_post=quoted_post,
                )
            except TwitterScreenshotRendererError as exc:
                logger.warning(
                    "Rust Twitter screenshot renderer failed; falling back to Python: %s",
                    exc,
                )
    except Exception as exc:
        logger.warning(
            "Could not initialize Rust Twitter screenshot renderer; falling back to Python: %s",
            exc,
        )

    return render_twitter_text_screenshot_python(
        tweet_text=tweet_text,
        temp_dir=temp_dir,
        tweet_identifier=tweet_identifier,
        tweet_info=tweet_info,
        quoted_post=quoted_post,
    )
