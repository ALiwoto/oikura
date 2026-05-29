from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_CONFIG_PATHS = (
    Path("config") / "config.ini",
    Path("config.ini"),
)


@dataclass(frozen=True, slots=True)
class PyrogramConfig:
    api_id: int
    api_hash: str
    bot_token: str


@dataclass(frozen=True, slots=True)
class PixivConfig:
    access_token: str = ""
    refresh_token: str = ""


@dataclass(frozen=True, slots=True)
class OikuraConfig:
    whitelisted_chats: frozenset[int | str]
    ffmpeg: str = "ffmpeg"


@dataclass(frozen=True, slots=True)
class AppConfig:
    pyrogram: PyrogramConfig
    oikura: OikuraConfig
    pixiv: PixivConfig
    path: Path


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _parse_whitelisted_chats(raw_value: str) -> frozenset[int | str]:
    values: list[int | str] = []
    for item in _split_csv(raw_value):
        normalized = item.removeprefix("@").strip()
        try:
            values.append(int(normalized))
        except ValueError:
            values.append(normalized.lower())

    return frozenset(values)


def _first_existing_path(paths: Iterable[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path

    return next(iter(paths))


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = Path(path) if path else _first_existing_path(DEFAULT_CONFIG_PATHS)
    if not config_path.exists():
        raise FileNotFoundError(
            f"config file was not found: {config_path}. "
            "Create config/config.ini or pass a config path."
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
        ),
        pixiv=PixivConfig(
            access_token=parser.get("pixiv", "access_token", fallback="").strip(),
            refresh_token=parser.get("pixiv", "refresh_token", fallback="").strip(),
        ),
        path=config_path,
    )
