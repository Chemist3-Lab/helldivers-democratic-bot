"""Async HTTP client for the official Steam News API.

Targeting Helldivers 2 (Steam App ID: 553850)
Endpoint:
https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/?appid=553850&count=5

Features:
- Filters patches, hotfixes, major updates, and warbonds
- HTML tag stripping and formatting cleanup
- In-memory caching with configurable TTL
- Strict Pydantic models for Steam news items
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from pydantic import BaseModel, Field
import httpx

from src.services._retry import async_retry

log = logging.getLogger(__name__)

STEAM_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/"
HELLDIVERS_2_APP_ID = 553850


# ─── Pydantic News Models ───────────────────────────────────────────────────


class SteamNewsItem(BaseModel):
    """Represents an individual Steam news item."""

    gid: str
    title: str
    url: str
    is_external_url: bool = Field(default=False, alias="is_external_url")
    author: str = Field(default="")
    contents: str = Field(default="")
    feedlabel: str = Field(default="")
    date: int = Field(default=0)  # Unix timestamp
    feedname: str = Field(default="")
    feed_type: int = Field(default=0)
    appid: int = Field(default=HELLDIVERS_2_APP_ID)

    @property
    def clean_contents(self) -> str:
        """Strip raw HTML tags, bbcode-like tags, and normalize spacing."""
        text = self.contents
        # Replace Steam BBCode links [url=...](...) or [url=...]...[/url]
        text = re.sub(r"\[url=([^\]]+)\](.*?)\[/url\]", r"[\2](\1)", text, flags=re.IGNORECASE)
        # Remove other BBCode tags like [b], [/b], [img], [list], [*]
        text = re.sub(r"\[/?(?:b|i|u|h[1-6]|img|list|\*|strike|code|quote|spoiler)[^\]]*\]", "", text, flags=re.IGNORECASE)
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # Collapse multiple newlines/spaces
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()

    @property
    def is_patch_or_update(self) -> bool:
        """Heuristic to check if this news item is a patch, hotfix, or warbond update."""
        combined = f"{self.title} {self.feedlabel}".lower()
        keywords = ("patch", "hotfix", "update", "warbond", "notes", "version", "balance")
        return any(kw in combined for kw in keywords)


class SteamNewsAppResponse(BaseModel):
    """Schema for appnews container in Steam API response."""

    appid: int
    newsitems: list[SteamNewsItem] = Field(default_factory=list)
    count: int = Field(default=0)


class SteamNewsResponse(BaseModel):
    """Root response model from Steam GetNewsForApp."""

    appnews: SteamNewsAppResponse


# ─── Service Client ──────────────────────────────────────────────────────────


class SteamApiClient:
    """Async client for fetching and filtering Steam News updates."""

    def __init__(
        self,
        app_id: int = HELLDIVERS_2_APP_ID,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self._custom_client = client
        self._client: httpx.AsyncClient | None = client
        self._cache: list[SteamNewsItem] | None = None
        self._cache_timestamp: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=10.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={"User-Agent": "Helldivers-Democratic-Bot/1.0", "Accept": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client is not None and not self._client.is_closed and self._custom_client is None:
            await self._client.aclose()
            self._client = None

    @async_retry(max_retries=3, base_delay=1.0, backoff_factor=2.0)
    async def _fetch_news(self, count: int = 10, max_length: int = 3000) -> list[SteamNewsItem]:
        client = await self._get_client()
        params = {
            "appid": self.app_id,
            "count": count,
            "maxlength": max_length,
            "format": "json",
        }
        response = await client.get(STEAM_NEWS_URL, params=params)
        response.raise_for_status()
        data = response.json()
        parsed = SteamNewsResponse.model_validate(data)
        return parsed.appnews.newsitems

    async def get_latest_news(
        self,
        count: int = 5,
        ttl: float = 540.0,
        only_patches: bool = False,
    ) -> list[SteamNewsItem]:
        """Fetch latest Steam news with caching (default TTL: 9 minutes).

        Args:
            count: Number of news items to return.
            ttl: Cache TTL in seconds.
            only_patches: If True, filters out community announcements that aren't patches/updates.
        """
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_timestamp) < ttl:
            items = self._cache
        else:
            items = await self._fetch_news(count=max(count, 10))
            self._cache = items
            self._cache_timestamp = now

        if only_patches:
            items = [item for item in items if item.is_patch_or_update]

        return items[:count]
