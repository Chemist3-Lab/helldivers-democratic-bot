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

from datetime import datetime
import logging
import re
import time
from typing import Any
from pydantic import BaseModel, Field
import httpx

from src.services._retry import async_retry

log = logging.getLogger(__name__)

BASE_URL = "https://api.helldivers2.dev/api/v1"
DEFAULT_SUPER_CLIENT = "HelldiversDemocracyBot"
DEFAULT_SUPER_CONTACT = "https://github.com/Chemist3-Lab/helldivers-democratic-bot"


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
    """Major Order data schema from /major-orders or /assignments."""

    id: int
    progress: list[int] = Field(default_factory=list)
    expires_in: int = Field(default=0, alias="expiresIn")
    expiration: str | None = Field(default=None)
    setting: dict[str, Any] = Field(default_factory=dict)
    raw_title: str | None = Field(default=None, alias="title")
    briefing: str | None = Field(default=None)
    description: str | None = Field(default=None)
    reward: dict[str, Any] | None = Field(default=None)
    tasks: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def title(self) -> str:
        """Extract title or provide fallback."""
        task_title = self.setting.get("overrideTitle") or self.setting.get("taskDescription")
        if task_title:
            return clean_game_markup(str(task_title))
        if self.description:
            return clean_game_markup(str(self.description))
        if self.raw_title and self.raw_title != "MAJOR ORDER":
            return clean_game_markup(str(self.raw_title))
        return f"Major Order #{self.id}"

    @property
    def brief(self) -> str:
        """Extract narrative briefing with game markup cleaned."""
        raw = (
            self.briefing
            or self.setting.get("overrideBrief")
            or self.description
            or self.setting.get("reward", {}).get("description")
            or ""
        )
        cleaned = clean_game_markup(str(raw)) if raw else ""
        if cleaned and cleaned != "No briefing provided.":
            return cleaned
        return ""

    @property
    def task_description(self) -> str:
        """Extract task description with game markup cleaned."""
        raw = self.setting.get("taskDescription") or self.description or ""
        return clean_game_markup(str(raw))

    @property
    def medal_reward(self) -> int:
        """Extract medal reward amount from the order."""
        if self.reward and isinstance(self.reward, dict):
            try:
                return int(self.reward.get("amount", 0))
            except (ValueError, TypeError):
                pass
        r = self.setting.get("reward", {})
        if isinstance(r, dict):
            try:
                return int(r.get("amount", 0))
            except (ValueError, TypeError):
                pass
        return 0

    @property
    def target_unix(self) -> int | None:
        """Calculate target Unix timestamp for Discord dynamic countdown."""
        if self.expires_in > 0:
            return int(time.time() + self.expires_in)
        if self.expiration:
            try:
                iso = self.expiration.replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso)
                return int(dt.timestamp())
            except Exception:
                pass
        return None

    @property
    def time_remaining_discord(self) -> str:
        """Render Discord dynamic countdown string, e.g. '<t:1726264800:R>'."""
        t_unix = self.target_unix
        if t_unix is not None:
            if t_unix <= int(time.time()):
                return "Expired"
            return f"<t:{t_unix}:R>"
        return "Pending High Command Update"

    @property
    def time_remaining_str(self) -> str:
        """Format remaining time into a human-readable countdown string."""
        secs = self.expires_in
        if secs <= 0 and self.expiration:
            t_unix = self.target_unix
            if t_unix is not None:
                secs = max(0, t_unix - int(time.time()))
        if secs <= 0:
            return "Expired"
        days = secs // 86400
        hours = (secs % 86400) // 3600
        minutes = (secs % 3600) // 60
        parts: list[str] = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 or not parts:
            parts.append(f"{minutes}m")
        return " ".join(parts)


def clean_game_markup(text: str) -> str:
    """Convert in-game formatting tags to Discord Markdown and strip unclosed XML tags.

    Handles:
    - <i=1>...</i=1>, <i=3>...</i=3>, </i> -> bold/italic Markdown
    - Strips leftover XML-like tokens
    """
    if not text:
        return ""

    # Replace <i=[0-9]> and matching </i=[0-9]> or </i> with **...**
    cleaned = re.sub(r"<i=[0-9]+>(.*?)(?:</i=[0-9]+>|</i>)", r"**\1**", text, flags=re.DOTALL)
    # Catch any remaining lone opening or closing <i=...> / </i> tags
    cleaned = re.sub(r"</?i(?:=[0-9]+)?>", "**", cleaned)
    # Strip any remaining unhandled or unclosed XML/HTML-like tags
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    # Clean up double bold marks (**** -> **)
    cleaned = re.sub(r"\*{4,}", "**", cleaned)
    # Normalize excessive blank lines
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
    return cleaned.strip()


class Dispatch(BaseModel):
    """High Command dispatch from /dispatches."""

    id: int
    published: str = Field(default="")
    type: int = Field(default=0)
    message: str = Field(default="")

    @property
    def clean_message(self) -> str:
        """Return dispatch message with game engine markup converted to Markdown."""
        return clean_game_markup(self.message)


class PlanetInfo(BaseModel):
    """Basic planetary information."""

    index: int
    name: str = Field(default="Unknown Planet")
    sector: str = Field(default="Unknown Sector")
    biome: dict[str, Any] = Field(default_factory=dict)
    hazard: dict[str, Any] = Field(default_factory=dict)
    hazards: list[dict[str, Any]] = Field(default_factory=list)
    max_health: int = Field(default=1000000, alias="maxHealth")
    current_health: int | None = Field(default=None, alias="currentHealth")
    health: int | None = Field(default=None)
    regen_per_second: float = Field(default=0.0, alias="regenPerSecond")
    liberation: float | None = Field(default=None)
    event: dict[str, Any] | None = Field(default=None)
    statistics: dict[str, Any] = Field(default_factory=dict)
    players: int | None = Field(default=None)

    @property
    def effective_health(self) -> int | None:
        """Return remaining enemy health pool on this planet."""
        if self.health is not None:
            return self.health
        return self.current_health

    @property
    def player_count(self) -> int:
        """Total Helldivers currently deployed on this planet."""
        if self.players is not None:
            return self.players
        if self.statistics and "playerCount" in self.statistics:
            try:
                return int(self.statistics["playerCount"])
            except (ValueError, TypeError):
                return 0
        return 0

    @property
    def liberation_pct(self) -> float:
        """Calculate liberation percentage (0.0 to 100.0).

        If liberation field is provided directly, use it.
        Otherwise, calculate from health and max_health:
        Enemy health decreases as Helldivers liberate the planet.
        liberation = 100.0 - ((planet.health / planet.max_health) * 100.0)
        """
        if self.liberation is not None:
            return max(min(float(self.liberation), 100.0), 0.0)

        eff_health = self.effective_health
        eff_max = self.max_health if self.max_health and self.max_health > 0 else 1000000

        if eff_health is None:
            return 0.0

        if eff_health <= 0:
            return 100.0

        if eff_health >= eff_max:
            return 0.0

        pct = 100.0 - ((float(eff_health) / float(eff_max)) * 100.0)
        return max(min(pct, 100.0), 0.0)

    @property
    def defense_health_pct(self) -> float:
        """Calculate defense event health percentage if planet is under defense campaign."""
        if self.event and isinstance(self.event, dict):
            ev_health = self.event.get("health")
            ev_max = self.event.get("maxHealth")
            if ev_health is not None and ev_max and ev_max > 0:
                return max(min((float(ev_health) / float(ev_max)) * 100.0, 100.0), 0.0)

        eff_health = self.effective_health
        if self.max_health > 0 and eff_health is not None and eff_health > 0:
            return max(min((float(eff_health) / float(self.max_health)) * 100.0, 100.0), 0.0)
        return 0.0


class Campaign(BaseModel):
    """Active planetary campaign from /campaigns."""

    id: int
    planet: PlanetInfo
    type: int = Field(default=0)
    count: int = Field(default=0)
    faction: str = Field(default="")

    @property
    def is_defense(self) -> bool:
        """Returns True if this campaign is a defense priority (type 2 or active event on planet)."""
        return self.type == 2 or (self.planet.event is not None and len(self.planet.event) > 0)

    @property
    def players(self) -> int:
        """Active Helldivers deployed on this campaign."""
        return self.planet.player_count or self.count



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
        client_name: str = DEFAULT_SUPER_CLIENT,
        contact: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_name = client_name
        self.contact = contact or DEFAULT_SUPER_CONTACT
        self._custom_client = client
        self._client: httpx.AsyncClient | None = client
        self._cache: dict[str, CacheEntry] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
                headers={
                    "User-Agent": f"{self.client_name}/1.0",
                    "X-Super-Client": self.client_name,
                    "X-Super-Contact": self.contact or DEFAULT_SUPER_CONTACT,
                    "Accept-Language": "en-US",
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
        clean_endpoint = endpoint.strip("/")
        url = f"{self.base_url}/{clean_endpoint}"
        response = await client.get(url)
        if response.status_code >= 400:
            log.error(
                "HD2 API request failed [%d %s] for URL %s. Response body: %s",
                response.status_code,
                response.reason_phrase,
                url,
                response.text,
            )
        response.raise_for_status()
        return response.json()

    async def get_major_orders(self, ttl: float = 270.0) -> list[MajorOrder]:
        """Fetch active Major Orders with caching (default TTL: 4.5 minutes).

        On api.helldivers2.dev/api/v1, active Major Orders are located at /assignments.
        Falls back to /major-orders and /major-order if needed, and catches 404 gracefully
        (which occurs when no Major Order is active).
        """
        cache_key = "major_orders"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        data: Any = None
        # Try endpoints in order: /assignments -> /major-orders -> /major-order
        endpoints = ["/assignments", "/major-orders", "/major-order"]
        for endpoint in endpoints:
            try:
                data = await self._request(endpoint)
                if data:
                    break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise

        if not data:
            log.info("No active Major Order found across endpoints: %s", endpoints)
            self._set_cache(cache_key, [], ttl)
            return []

        # Some endpoints return a single object or a list of objects
        if isinstance(data, dict):
            orders = [MajorOrder.model_validate(data)]
        elif isinstance(data, list):
            orders = [MajorOrder.model_validate(item) for item in data]
        else:
            orders = []

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

    async def get_latest_major_order_dispatch(self) -> str | None:
        """Fetch the most recent dispatch message regarding a Major Order.

        Used as a fallback when /assignments or /major-orders has an empty briefing.
        Looks for dispatches containing '<i=3>NEW MAJOR ORDER' or 'MAJOR ORDER'.
        """
        try:
            dispatches = await self.get_dispatches()
            for item in dispatches:
                raw = item.message
                if "<i=3>NEW MAJOR ORDER" in raw or "MAJOR ORDER" in raw:
                    return item.clean_message
        except Exception as exc:
            log.warning("Failed to fetch Major Order dispatch fallback: %s", exc)
        return None


    async def get_campaigns(self, ttl: float = 270.0) -> list[Campaign]:
        """Fetch active campaigns with caching (default TTL: 4.5 minutes).

        Falls back to https://api.diveharder.com/v1/all_status if community API fails
        or returns empty campaign data.
        """
        cache_key = "campaigns"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        campaigns: list[Campaign] = []
        try:
            data = await self._request("/campaigns")
            if isinstance(data, list):
                campaigns = [Campaign.model_validate(item) for item in data]
        except Exception as exc:
            log.warning("Primary /campaigns endpoint failed: %s. Attempting diveharder mirror...", exc)

        # Fallback to api.diveharder.com/v1/all_status if primary failed or empty
        if not campaigns:
            try:
                client = await self._get_client()
                mirror_resp = await client.get("https://api.diveharder.com/v1/all_status")
                if mirror_resp.status_code == 200:
                    mirror_data = mirror_resp.json()
                    raw_campaigns = mirror_data.get("campaigns") or []
                    for item in raw_campaigns:
                        try:
                            campaigns.append(Campaign.model_validate(item))
                        except Exception:
                            pass
            except Exception as mirror_exc:
                log.warning("Fallback mirror api.diveharder.com/v1/all_status failed: %s", mirror_exc)

        self._set_cache(cache_key, campaigns, ttl)
        return campaigns

    async def get_planets_map(self, ttl: float = 86400.0) -> dict[int, str]:
        """Fetch index -> planet name mapping with caching (default TTL: 24 hours)."""
        cache_key = "planets_map"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]

        planets_map: dict[int, str] = {}
        try:
            data = await self._request("/planets")
            if isinstance(data, list):
                for p in data:
                    idx = p.get("index")
                    name = p.get("name")
                    if idx is not None and name:
                        planets_map[int(idx)] = str(name).strip()
        except Exception as exc:
            log.warning("Failed to fetch /planets map: %s", exc)

        if planets_map:
            self._set_cache(cache_key, planets_map, ttl)
        return planets_map
