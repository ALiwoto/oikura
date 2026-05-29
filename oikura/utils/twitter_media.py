import asyncio
import logging
import os
import tempfile
from typing import Any, Dict, Optional, Protocol
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


class ChunkedContent(Protocol):
    def iter_chunked(self, size: int): ...


class HttpResponse(Protocol):
    content: ChunkedContent

    def raise_for_status(self) -> None: ...


class HttpClient(Protocol):
    def get(self, url: str): ...


def get_twitter_media_kind(
    media_url: str,
    media_info: Optional[Dict[str, Any]] = None,
) -> str:
    media_info = media_info or {}
    media_type = str(media_info.get("type") or "").lower()
    extension = str(media_info.get("extension") or "").lower()
    if not extension:
        file_name = urlparse(media_url).path.rsplit("/", maxsplit=1)[-1]
        if "." in file_name:
            extension = file_name.rsplit(".", maxsplit=1)[-1].lower()

    if media_type in {"video", "animated_gif"} or extension in {
        "mp4",
        "mov",
        "m4v",
        "webm",
    }:
        return "video"

    if media_type == "photo" or extension in {"jpg", "jpeg", "png", "webp"}:
        return "photo"

    return "document"


def get_twitter_file_name(
    tweet_identifier: str,
    media_url: str,
    media_kind: str,
    counter: Optional[int] = None,
    media_info: Optional[Dict[str, Any]] = None,
) -> str:
    media_info = media_info or {}
    extension = str(media_info.get("extension") or "").lower()
    if not extension:
        url_name = urlparse(media_url).path.rsplit("/", maxsplit=1)[-1]
        if "." in url_name:
            extension = url_name.rsplit(".", maxsplit=1)[-1].lower()

    if not extension:
        extension = {
            "photo": "jpg",
            "video": "mp4",
        }.get(media_kind, "bin")

    suffix = "" if counter is None else f"_{counter}"
    return f"{tweet_identifier}{suffix}.{extension}"


def build_twitter_temp_path(temp_dir: str, file_name: str) -> str:
    safe_name = os.path.basename(file_name) or "twitter_media.bin"
    return os.path.join(temp_dir, safe_name)


async def download_twitter_media(
    http_client: HttpClient,
    url: str,
    file_name: str,
    temp_dir: str,
) -> str:
    media_path = build_twitter_temp_path(temp_dir=temp_dir, file_name=file_name)
    async with http_client.get(url=url) as response:
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        with open(media_path, "wb") as media_file:
            async for chunk in response.content.iter_chunked(1024 * 256):
                if chunk:
                    media_file.write(chunk)
    return media_path


def get_positive_int(value: Any) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0

    return value if value > 0 else 0


def get_ffprobe_path(ffmpeg_path: str) -> str:
    base_name = os.path.basename(ffmpeg_path).lower()
    if base_name in {"ffmpeg", "ffmpeg.exe"}:
        target_name = "ffprobe.exe" if base_name.endswith(".exe") else "ffprobe"
        return os.path.join(os.path.dirname(ffmpeg_path), target_name) or target_name

    return "ffprobe"


async def probe_twitter_video_dimensions(
    video_path: str,
    ffmpeg_path: str = "ffmpeg",
) -> tuple[int, int]:
    ffprobe_path = get_ffprobe_path(ffmpeg_path)
    try:
        process = await asyncio.create_subprocess_exec(
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
    except FileNotFoundError:
        logger.warning("ffprobe was not found while probing twitter video dimensions")
        return 0, 0
    except Exception as ex:
        logger.warning(
            f"failed to probe twitter video dimensions: {ex}",
            stacklevel=3,
        )
        return 0, 0

    if process.returncode != 0:
        logger.warning(
            "ffprobe failed while probing twitter video dimensions: %s",
            stderr.decode(errors="ignore").strip(),
            stacklevel=3,
        )
        return 0, 0

    raw_size = stdout.decode(errors="ignore").strip()
    if "x" not in raw_size:
        return 0, 0

    width_text, height_text = raw_size.split("x", maxsplit=1)
    return get_positive_int(width_text), get_positive_int(height_text)


async def generate_twitter_video_thumb(
    video_path: str,
    temp_dir: str,
    ffmpeg_path: str = "ffmpeg",
) -> Optional[str]:
    thumb_name = (
        f"{os.path.splitext(os.path.basename(video_path))[0] or 'twitter_video'}_thumb.jpg"
    )
    thumb_path = build_twitter_temp_path(temp_dir=temp_dir, file_name=thumb_name)
    try:
        process = await asyncio.create_subprocess_exec(
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            video_path,
            "-vf",
            "thumbnail,scale=320:-1:force_original_aspect_ratio=decrease",
            "-frames:v",
            "1",
            thumb_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
    except FileNotFoundError:
        logger.warning("ffmpeg was not found while creating twitter video thumbnail")
        return None
    except Exception as ex:
        logger.warning(
            f"failed to create twitter video thumbnail: {ex}",
            stacklevel=3,
        )
        return None

    if (
        process.returncode != 0
        or not os.path.exists(thumb_path)
        or os.path.getsize(thumb_path) <= 0
    ):
        logger.warning(
            "ffmpeg failed while creating twitter video thumbnail: %s",
            stderr.decode(errors="ignore").strip(),
            stacklevel=3,
        )
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
        return None

    return thumb_path


async def get_twitter_video_upload_args(
    media_path: str,
    temp_dir: str,
    ffmpeg_path: str = "ffmpeg",
    media_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    media_info = media_info or {}
    width = get_positive_int(media_info.get("width"))
    height = get_positive_int(media_info.get("height"))

    if not width or not height:
        width, height = await probe_twitter_video_dimensions(
            video_path=media_path,
            ffmpeg_path=ffmpeg_path,
        )

    return {
        "width": width,
        "height": height,
        "thumb": await generate_twitter_video_thumb(
            video_path=media_path,
            temp_dir=temp_dir or tempfile.gettempdir(),
            ffmpeg_path=ffmpeg_path,
        ),
    }
