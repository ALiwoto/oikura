from __future__ import annotations

import atexit
import datetime
import json
import os
from pathlib import Path
import subprocess
import threading
from collections.abc import Iterable
from typing import Any, Dict, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
RENDERER_TARGET_DIR = REPO_ROOT / "rust_renderer" / "target"
RENDERER_BINARY_NAME = "oikura-rust-render.exe" if os.name == "nt" else "oikura-rust-render"


class TwitterScreenshotRendererError(RuntimeError):
    pass


def _normalize_for_json(value: Any) -> Any:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_json(item) for item in value]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, str)):
        return [_normalize_for_json(item) for item in value]
    return value


def _quoted_post_payload(quoted_post: Any) -> Optional[Dict[str, Any]]:
    if not quoted_post:
        return None
    return {
        "text": _normalize_for_json(getattr(quoted_post, "text", "") or ""),
        "info": _normalize_for_json(getattr(quoted_post, "info", None) or {}),
        "media": [
            {
                "url": _normalize_for_json(getattr(media, "url", "") or ""),
                "info": _normalize_for_json(getattr(media, "info", None) or {}),
            }
            for media in (getattr(quoted_post, "media", None) or [])
        ],
    }


class TwitterScreenshotRenderer:
    def __init__(self, binary_path: Path) -> None:
        self.binary_path = binary_path
        self._process: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()
        self._next_id = 1

    def close(self) -> None:
        process = self._process
        self._process = None
        if not process:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except Exception:
            pass
        try:
            process.terminate()
            process.wait(timeout=1)
        except Exception:
            process.kill()

    def _start(self) -> subprocess.Popen[str]:
        if not self.binary_path.exists():
            raise TwitterScreenshotRendererError(
                f"Rust renderer binary is missing: {self.binary_path}"
            )

        process = subprocess.Popen(
            [str(self.binary_path), "--serve"],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env={**os.environ, "OIKURA_RENDER_REPO_ROOT": str(REPO_ROOT)},
        )
        self._process = process
        return process

    def _get_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None or process.poll() is not None:
            return self._start()
        return process

    def render(
        self,
        *,
        tweet_text: str,
        temp_dir: str,
        tweet_identifier: str,
        tweet_info: Optional[Dict[str, Any]] = None,
        quoted_post: Any = None,
    ) -> str:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            payload = {
                "id": request_id,
                "tweet_text": tweet_text,
                "temp_dir": temp_dir,
                "tweet_identifier": tweet_identifier,
                "tweet_info": _normalize_for_json(tweet_info or {}),
                "quoted_post": _quoted_post_payload(quoted_post),
            }

            process = self._get_process()
            if process.stdin is None or process.stdout is None:
                raise TwitterScreenshotRendererError("Rust renderer pipes are not available")

            try:
                process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                process.stdin.flush()
                response_line = process.stdout.readline()
            except Exception as exc:
                self.close()
                raise TwitterScreenshotRendererError(f"Rust renderer IPC failed: {exc}") from exc

            if not response_line:
                self.close()
                raise TwitterScreenshotRendererError("Rust renderer exited without a response")

            try:
                response = json.loads(response_line)
            except json.JSONDecodeError as exc:
                raise TwitterScreenshotRendererError(
                    f"Rust renderer returned invalid JSON: {response_line[:200]!r}"
                ) from exc

            if response.get("id") != request_id:
                raise TwitterScreenshotRendererError(
                    f"Rust renderer response id mismatch: {response.get('id')} != {request_id}"
                )
            if not response.get("ok"):
                raise TwitterScreenshotRendererError(
                    str(response.get("error") or "Rust renderer failed")
                )

            output_path = str(response.get("path") or "")
            if not output_path:
                raise TwitterScreenshotRendererError("Rust renderer returned an empty output path")
            return output_path


_renderer: Optional[TwitterScreenshotRenderer] = None


def get_twitter_screenshot_renderer_bin() -> Path:
    configured_bin = os.environ.get("OIKURA_RUST_RENDER_BIN")
    if configured_bin:
        return Path(configured_bin)

    profile = os.environ.get("OIKURA_RUST_RENDER_PROFILE", "release").strip().lower()
    if profile in {"debug", "dev", "development"}:
        target_profile = "debug"
    elif profile in {"release", "prod", "production"}:
        target_profile = "release"
    else:
        target_profile = profile

    return RENDERER_TARGET_DIR / target_profile / RENDERER_BINARY_NAME


def has_twitter_screenshot_renderer_bin() -> bool:
    return get_twitter_screenshot_renderer_bin().is_file()


def get_twitter_screenshot_renderer() -> TwitterScreenshotRenderer:
    global _renderer
    if _renderer is None:
        _renderer = TwitterScreenshotRenderer(binary_path=get_twitter_screenshot_renderer_bin())
        atexit.register(_renderer.close)
    return _renderer


def render_twitter_text_screenshot(
    tweet_text: str,
    temp_dir: str,
    tweet_identifier: str,
    tweet_info: Optional[Dict[str, Any]] = None,
    quoted_post: Any = None,
) -> str:
    return get_twitter_screenshot_renderer().render(
        tweet_text=tweet_text,
        temp_dir=temp_dir,
        tweet_identifier=tweet_identifier,
        tweet_info=tweet_info,
        quoted_post=quoted_post,
    )
