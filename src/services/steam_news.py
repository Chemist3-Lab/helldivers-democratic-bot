"""Backward compatibility alias for steam_api."""

from src.services.steam_api import (
    SteamApiClient,
    SteamNewsItem,
    SteamNewsResponse,
    SteamNewsAppResponse,
)

__all__ = [
    "SteamApiClient",
    "SteamNewsItem",
    "SteamNewsResponse",
    "SteamNewsAppResponse",
]
