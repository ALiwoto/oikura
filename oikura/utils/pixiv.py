from __future__ import annotations

import datetime
import logging
from typing import Any

from pixivpy3 import AppPixivAPI


logger = logging.getLogger(__name__)


class PixivIllustInfo:
    def __init__(self, illust_json: Any) -> None:
        self.has_error = False
        self.error_message = ""
        self.user_error_message = ""
        self.error_reason = ""
        self.is_multiple = False
        self.meta_pages = []

        error = getattr(illust_json, "error", None)
        if error:
            self.has_error = True
            self.error_message = getattr(error, "message", "") or ""
            self.user_error_message = getattr(error, "user_message", "") or ""
            self.error_reason = getattr(error, "reason", "") or ""
            return

        illust = getattr(illust_json, "illust", None)
        meta_pages = getattr(illust, "meta_pages", None) or []
        self.is_multiple = bool(meta_pages)
        self.meta_pages = meta_pages


def do_pixiv_auth(
    pixiv_api: AppPixivAPI,
    *,
    access_token: str,
    refresh_token: str,
) -> None:
    if not pixiv_api.access_token and access_token:
        try:
            pixiv_api.set_auth(
                refresh_token=refresh_token,
                access_token=access_token,
            )
            auth_result = pixiv_api.auth()

            auth_date = datetime.datetime.now()
            expires_in = auth_result.expires_in
            setattr(pixiv_api, "auth_date", auth_date)
            setattr(pixiv_api, "auth_expires_in", expires_in)
            setattr(
                pixiv_api,
                "auth_expiration_date",
                auth_date + datetime.timedelta(seconds=expires_in),
            )
        except Exception as ex:
            logger.warning("failed to auth pixiv with saved tokens: %s", ex, stacklevel=3)
    elif pixiv_api.refresh_token:
        if (
            getattr(pixiv_api, "auth_expiration_date", None)
            and datetime.datetime.now() < pixiv_api.auth_expiration_date
        ):
            return

        pixiv_api.access_token = None
        return do_pixiv_auth(
            pixiv_api,
            access_token=access_token,
            refresh_token=refresh_token,
        )
