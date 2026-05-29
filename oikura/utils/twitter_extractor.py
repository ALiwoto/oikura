from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from gallery_dl import util
from gallery_dl.extractor import find as find_extractor
from gallery_dl.extractor.message import Message as ExtractorMessage
from gallery_dl.extractor.twitter import TwitterTweetExtractor


@dataclass(slots=True)
class TwitterDirectMedia:
    url: str
    info: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TwitterPostData:
    source_url: str = ""
    text: str = ""
    info: Dict[str, Any] = field(default_factory=dict)
    media: List[TwitterDirectMedia] = field(default_factory=list)
    quoted_post: Optional["TwitterPostData"] = None

    @property
    def identifier(self) -> str:
        return get_twitter_post_identifier(self.source_url)


def get_twitter_post_identifier(url: str) -> str:
    parsed_url = urlparse(url)
    return parsed_url.path.rstrip("/").split("/")[-1] or "twitter"


def build_twitter_post_url(tweet_info: Dict[str, Any]) -> str:
    tweet_id = str(tweet_info.get("tweet_id") or "").strip()
    author_info = tweet_info.get("author")
    author_handle = ""
    if isinstance(author_info, dict):
        author_handle = str(author_info.get("name") or "").strip().lstrip("@")

    if tweet_id and author_handle:
        return f"https://x.com/{author_handle}/status/{tweet_id}"
    if tweet_id:
        return f"https://x.com/i/web/status/{tweet_id}"
    return ""


def patch_twitter_user_transform(extractor: TwitterTweetExtractor) -> None:
    original_transform = getattr(extractor, "_transform_user", None)
    if not callable(original_transform) or original_transform is util.identity:
        return

    def transform_user_with_x_details(user: Dict[str, Any]) -> Dict[str, Any]:
        transformed_user = original_transform(user)
        if not isinstance(transformed_user, dict) or not isinstance(user, dict):
            return transformed_user

        if user.get("is_blue_verified"):
            transformed_user["blue_verified"] = True

        professional_info = user.get("professional")
        if isinstance(professional_info, dict):
            professional_type = professional_info.get("professional_type")
            if professional_type:
                transformed_user["professional_type"] = professional_type

        profile_image_shape = user.get("profile_image_shape")
        if profile_image_shape:
            transformed_user["profile_image_shape"] = profile_image_shape

        return transformed_user

    extractor._transform_user = transform_user_with_x_details


def extract_twitter_post_data(url: str) -> Optional[TwitterPostData]:
    extractor: TwitterTweetExtractor = find_extractor(url)
    if not extractor:
        return None

    extractor.initialize()
    patch_twitter_user_transform(extractor)
    if hasattr(extractor, "textonly"):
        extractor.textonly = True
    if hasattr(extractor, "quoted"):
        extractor.quoted = True

    post_data = TwitterPostData(source_url=url)
    current_post: Optional[TwitterPostData] = post_data

    for msg in extractor.items():
        if not isinstance(msg, tuple):
            continue

        if msg[0] == ExtractorMessage.Directory and len(msg) > 1:
            tweet_info = msg[1]
            if isinstance(tweet_info, dict):
                tweet_info = dict(tweet_info)
                if not post_data.info:
                    current_post = post_data
                elif post_data.quoted_post is None:
                    current_post = TwitterPostData(
                        source_url=build_twitter_post_url(tweet_info),
                    )
                    post_data.quoted_post = current_post
                else:
                    current_post = None
                    continue

                current_post.info = tweet_info
                current_post.text = str(tweet_info.get("content") or "").strip()
            continue

        if msg[0] == ExtractorMessage.Url and current_post is not None:
            current_post.media.append(
                TwitterDirectMedia(
                    url=msg[1],
                    info=(msg[2] if len(msg) > 2 and isinstance(msg[2], dict) else {}),
                )
            )

    return post_data
