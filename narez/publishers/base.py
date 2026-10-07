from __future__ import annotations
from abc import ABC, abstractmethod


REAL_PLATFORMS = {"youtube", "instagram", "tiktok"}  # платформы с настоящей публикацией (остальные «сухие»)
NATIVE_SCHEDULE = {"youtube"}  # где отложенную публикацию делает сама платформа; у остальных время держит narez
PLATFORM_TITLES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}


def token_path(cfg: dict, platform: str, account_id: int):
    d = cfg["paths"]["workdir"] / "tokens"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{platform}_{account_id}.json"


class Publisher(ABC):
    platform: str

    @abstractmethod
    def publish(self, video_path: str, title: str, description: str, credentials: dict,
                publish_at=None, tags: list[str] | None = None) -> str:
        """Загружает видео, возвращает URL/ID. publish_at — datetime (UTC) отложенной публикации или None."""


class DryRunPublisher(Publisher):
    def __init__(self, platform: str):
        self.platform = platform

    def publish(self, video_path, title, description, credentials, publish_at=None, tags=None):
        when = f" на {publish_at:%H:%M}" if publish_at else ""
        return f"[dry-run:{self.platform}]{when} {title}"


def get_publisher(platform: str, cfg: dict | None = None) -> Publisher:
    if platform == "youtube" and cfg is not None:
        from .youtube import YouTubePublisher
        return YouTubePublisher(cfg)
    if platform == "instagram" and cfg is not None:
        from .instagram import InstagramPublisher
        return InstagramPublisher(cfg)
    if platform == "tiktok" and cfg is not None:
        from .tiktok import TikTokPublisher
        return TikTokPublisher(cfg)
    return DryRunPublisher(platform)
