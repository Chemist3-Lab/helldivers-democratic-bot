"""Async HTTP client for the Helldivers 2 Community API.

Client for https://api.helldivers2.dev/api/v1/
Endpoints:
- /major-orders: Current Major Order briefings and tasks
- /dispatches: High command dispatches and war updates
- /campaigns: Active planetary campaigns and defense priorities

Features:
- Custom 'X-Super-Client' header
- In-memory cache with configurable TTL and stale-while-revalidate
- Exponential backoff retry via async_retry
- Strictly typed Pydantic models
"""

from __future__ import annotations

import logging
import time
from typing import Any
from pydantic import BaseModel, Field
import httpx

from src.services._retry import async_retry

log = logging.getLogger(__name__)

BASE_URL = "https://api.helldivers2.dev/api/v1"
SUPER_CLIENT_HEADER = "Helldivers-Democratic-Bot/1.0 (Ministry of Truth; +https://github.com/Chemist3-Lab/helldivers-democratic-bot)"


# ─── Pydantic Response Models ────────────────────────────────────────────────


class MajorOrderTask(BaseModel):
    """Specific task or objective within a Major Order."""

    type: int = Field(default=0)
    values: list[int] = Field(default_factory=list)
    value_types: list[int] = Field(default_factory=list, alias="valueTypes")


class MajorOrderReward(BaseModel):
    """Reward granted upon completion of a Major Order."""

    type: int = Field(default=0)
    amount: int = Field(default=0)


class MajorOrder(BaseModel):
    """Major Order data schema from /major-orders."""

    id: int
    progress: list[int] = Field(default_factory=list)
    expires_in: int = Field(default=0, alias="expiresIn")
    setting: dict[str, Any] = Field(default_factory=dict)

    @property
    def title(self) -> str:
        """Extract title or provide fallback."""
        task_title = self.setting.get("overrideTitle") or self.setting.get("taskDescription")
        if task_title:
            return str(task_title)
        return f"Major Order #{self.id}"

    @property
    def brief(self) -> str:
        """Extract narrative briefing."""
        return str(self.setting.get("overrideBrief") or self.setting.get("reward", {}).get("description", "No briefing provided."))

    @property
    def task_description(self) -> str:
        """Extract task description."""
        return str(self.setting.get("taskDescription") or "")


class Dispatch(BaseModel):
    """High Command dispatch from /dispatches."""

    id: int
    published: str = Field(default="")
    type: int = Field(default=0)
    message: str = Field(default="")


class PlanetInfo(BaseModel):
    """Basic planetary information."""

    index: int
    name: str = Field(default="Unknown Planet")
    sector: str = Field(default="Unknown Sector")
    biome: dict[str, Any] = Field(default_factory=dict)
    hazard: dict[str, Any] = Field(default_factory=dict)
    max_health: int = Field(default=1000000, alias="maxHealth")
    current_health: int = Field(default=0, alias="currentHealth")
    regen_per_second: float = Field(default=0.0, alias="regenPerSecond")


class Campaign(BaseModel):
    """Active planetary campaign from /campaigns."""

    id: int
    planet: PlanetInfo
    type: int = Field(default=0)
    count: int = Field(default=0)

    @property
    def is_defense(self) -> bool:
        """Returns True if this campaign is a defense priority (type 2 or active event)."""
        return self.type == 2 or self.type == 1


# ─── Service Client ──────────────────────────────────────────────────────────


class CacheEntry:
    """Simple cache entry container."""

    __slots__ = ("data", "timestamp", "ttl")

    def __init__(self, data: Any, ttl: float) -> None:
        self.data: Any = data
        self.timestamp: float = time.monotonic()
        self.ttl: float = ttl

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.timestamp) > self.ttl


class HD2ApiClient:
    """Async API Client for the Helldivers 2 Community API."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._custom_client = client
        self._client: httpx.AsyncClient | None = client
        self._cache: dict[str, CacheEntry] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
                headers={
                    "User-Agent": "Helldivers-Democratic-Bot/1.0",
                    "X-Super-Client": SUPER_CLIENT_HEADER,
                    "Accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client is not None and not self._client.is_closed and self._custom_client is None:
            await self._client.aclose()
            self._client = None

    def _get_from_cache(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry and not entry.is_expired:
            return entry.data
        return None

    def _set_cache(self, key: str, data: Any, ttl: float) -> None:
        self._cache[key] = CacheEntry(data=data, ttl=ttl)

    @async_retry(max_retries=3, base_delay=1.0, backoff_factor=2.0)
    async def _request(self, endpoint: str) -> Any:
        client = await self._get_client()
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = await client.get(url)
        response.raise_for_status()
        return response.json()

    async def get_major_orders(self, ttl: float = 270.0) -> list[MajorOrder]:
        """Fetch active Major Orders with caching (default TTL: 4.5 minutes)."""
        cache_key = "major_orders"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        data = await self._request("/major-orders")
        orders = [MajorOrder.model_validate(item) for item in data]
        self._set_cache(cache_key, orders, ttl)
        return orders

    async def get_dispatches(self, ttl: float = 150.0) -> list[Dispatch]:
        """Fetch war dispatches with caching (default TTL: 2.5 minutes)."""
        cache_key = "dispatches"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        data = await self._request("/dispatches")
        dispatches = [Dispatch.model_validate(item) for item in data]
        self._set_cache(cache_key, dispatches, ttl)
        return dispatches

    async def get_campaigns(self, ttl: float = 270.0) -> list[Campaign]:
        """Fetch active campaigns with caching (default TTL: 4.5 minutes)."""
        cache_key = "campaigns"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        data = await self._request("/campaigns")
        campaigns = [Campaign.model_validate(item) for item in data]
        self._set_cache(cache_key, campaigns, ttl)
        return campaigns
