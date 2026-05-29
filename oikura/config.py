from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
import re


DEFAULT_CONFIG_PATHS = (
    Path("config.ini"),
)


@dataclass(slots=True)
class PyrogramConfig:
    api_id: int
    api_hash: str
    bot_token: str


@dataclass(slots=True)
class PixivConfig:
    access_token: str = ""
    refresh_token: str = ""


@dataclass(slots=True)
class OikuraConfig:
    whitelisted_chats: set[int | str]
    ffmpeg: str = "ffmpeg"
    owner_id: int = 0


@dataclass(slots=True)
class AppConfig:
    pyrogram: PyrogramConfig
    oikura: OikuraConfig
    pixiv: PixivConfig
    path: Path


def _split_config_list(value: str) -> list[str]:
    return [part for part in re.split(r"[,\s]+", value or "") if part]


def parse_chat_id(value: str | int) -> int | str:
    normalized = str(value).removeprefix("@").strip()
    try:
        return int(normalized)
    except ValueError:
        return normalized.lower()


def _parse_whitelisted_chats(raw_value: str) -> set[int | str]:
    values: list[int | str] = []
    for item in _split_config_list(raw_value):
        values.append(parse_chat_id(item))

    return set(values)


def _first_existing_path(paths: tuple[Path, ...]) -> Path:
    for path in paths:
        if path.exists():
            return path

    return paths[0]


def format_whitelisted_chats(values: set[int | str]) -> str:
    return ", ".join(str(item) for item in sorted(values, key=lambda item: str(item)))


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else _first_existing_path(DEFAULT_CONFIG_PATHS)
    if not config_path.exists():
        raise FileNotFoundError(
            f"config file was not found: {config_path}. "
            "Create config.ini or pass a config path with -c."
        )

    parser = ConfigParser()
    parser.read(config_path, encoding="utf-8")

    api_id = parser.getint("pyrogram", "api_id", fallback=0)
    api_hash = parser.get("pyrogram", "api_hash", fallback="").strip()
    bot_token = parser.get("pyrogram", "bot_token", fallback="").strip()
    if not api_id or not api_hash or not bot_token:
        raise ValueError("pyrogram.api_id, pyrogram.api_hash, and pyrogram.bot_token are required")

    whitelist = _parse_whitelisted_chats(
        parser.get("oikura", "whitelisted_chats", fallback=""),
    )
    if not whitelist:
        raise ValueError("oikura.whitelisted_chats must contain at least one chat id")

    return AppConfig(
        pyrogram=PyrogramConfig(
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
        ),
        oikura=OikuraConfig(
            whitelisted_chats=whitelist,
            ffmpeg=parser.get("oikura", "ffmpeg", fallback="ffmpeg").strip() or "ffmpeg",
            owner_id=parser.getint("oikura", "owner_id", fallback=0),
        ),
        pixiv=PixivConfig(
            access_token=parser.get("pixiv", "access_token", fallback="").strip(),
            refresh_token=parser.get("pixiv", "refresh_token", fallback="").strip(),
        ),
        path=config_path,
    )


def save_config(config: AppConfig) -> None:
    parser = ConfigParser()
    parser.read(config.path, encoding="utf-8")

    if not parser.has_section("oikura"):
        parser.add_section("oikura")

    parser.set(
        "oikura",
        "whitelisted_chats",
        format_whitelisted_chats(config.oikura.whitelisted_chats),
    )
    parser.set("oikura", "ffmpeg", config.oikura.ffmpeg)
    parser.set("oikura", "owner_id", str(config.oikura.owner_id))

    with config.path.open("w", encoding="utf-8") as config_file:
        parser.write(config_file)


def add_whitelisted_chat(config: AppConfig, chat_id: str | int) -> int | str:
    normalized = parse_chat_id(chat_id)
    config.oikura.whitelisted_chats.add(normalized)
    save_config(config)
    return normalized


def remove_whitelisted_chat(config: AppConfig, chat_id: str | int) -> int | str:
    normalized = parse_chat_id(chat_id)
    config.oikura.whitelisted_chats.discard(normalized)
    save_config(config)
    return normalized
