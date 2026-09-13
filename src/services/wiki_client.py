"""MediaWiki API Client and caching layer for helldivers.wiki.gg.

Endpoints & Operations:
- Uses MediaWiki Action API at `https://helldivers.wiki.gg/api.php`
- `action=opensearch`: Search for wiki articles by keyword
- `action=parse`: Fetch parsed wikitext/HTML or plain wikitext
- Persistent disk caching with a 24-hour TTL
- Offloads CPU-bound HTML parsing to `src.services.wiki_parser` via `asyncio.to_thread`
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
import httpx

from src.services._retry import async_retry
from src.services.wiki_models import WikiArticle, WikiSearchResult
from src.services.wiki_parser import (
    _clean_soup_html,
    _format_table_markdown,
    clean_soup_html,
    format_table_markdown,
)

# Re-export models and parser functions for backward compatibility
# Note: Core DOM parsing for druid-infobox, druid-label, druid-data,
# and Stratagem.?Arrow icons (mapping ➡, ⬆, ⬇, ⬅) is delegated to
# src.services.wiki_parser.clean_soup_html.
__all__ = [
    "CACHE_DIR",
    "MEDIAWIKI_API_URL",
    "WikiArticle",
    "WikiClient",
    "WikiSearchResult",
    "_clean_soup_html",
    "_format_table_markdown",
    "clean_soup_html",
    "format_table_markdown",
]

log = logging.getLogger(__name__)

MEDIAWIKI_API_URL = "https://helldivers.wiki.gg/api.php"
CACHE_DIR = Path("wiki_chunks")


class WikiClient:
    """Async client for searching and parsing the Helldivers.wiki.gg MediaWiki API."""

    def __init__(
        self,
        api_url: str = MEDIAWIKI_API_URL,
        cache_dir: Path = CACHE_DIR,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_url = api_url
        self.cache_dir = cache_dir
        self._custom_client = client
        self._client: httpx.AsyncClient | None = client

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=10.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={
                    "User-Agent": "Helldivers-Democratic-Bot/1.0 (Ministry of Truth Intelligence)",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client session."""
        if self._client is not None and not self._client.is_closed and self._custom_client is None:
            await self._client.aclose()
            self._client = None

    def _get_cache_path(self, title: str) -> Path:
        sanitized = re.sub(r"[^\w\-_\.]", "_", title.lower())
        return self.cache_dir / f"{sanitized}.json"

    def _read_disk_cache(self, title: str, ttl: float = 86400.0) -> WikiArticle | None:
        cache_path = self._get_cache_path(title)
        if not cache_path.exists():
            return None
        try:
            raw = cache_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if (time.time() - data.get("fetched_at", 0)) < ttl:
                # If cached article has empty infobox, re-parse with upgraded parser
                if not data.get("infobox"):
                    return None
                return WikiArticle.model_validate(data)
        except Exception:
            log.warning("Corrupt wiki cache for '%s', re-fetching.", title)
        return None

    def _write_disk_cache(self, article: WikiArticle) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._get_cache_path(article.title)
        try:
            cache_path.write_text(article.model_dump_json(indent=2), encoding="utf-8")
        except Exception:
            log.warning("Failed to write wiki cache for '%s'", article.title)

    @async_retry(max_retries=2, base_delay=2.0, backoff_factor=2.0)
    async def search(self, query: str, limit: int = 5) -> list[WikiSearchResult]:
        """Search the wiki using OpenSearch API with MediaWiki full-text search fallback.

        If opensearch returns no matches or low confidence, falls back to:
        api.php?action=query&list=search&srsearch={query}&utf8=&format=json
        extracting the matching article title and section anchor.
        """
        client = await self._get_client()
        clean_query = query.strip()
        if not clean_query:
            return []

        results: list[WikiSearchResult] = []

        # 1. Try opensearch first
        try:
            params = {
                "action": "opensearch",
                "search": clean_query,
                "limit": limit,
                "namespace": 0,
                "format": "json",
            }
            response = await client.get(self.api_url, params=params)
            response.raise_for_status()
            data = response.json()

            if isinstance(data, list) and len(data) >= 4 and data[1]:
                titles: list[str] = data[1]
                descriptions: list[str] = data[2]
                urls: list[str] = data[3]
                for i, title in enumerate(titles):
                    desc = descriptions[i] if i < len(descriptions) else ""
                    url = (
                        urls[i]
                        if i < len(urls)
                        else f"https://helldivers.wiki.gg/wiki/{title.replace(' ', '_')}"
                    )
                    results.append(WikiSearchResult(title=title, url=url, description=desc))
        except Exception as e:
            log.warning("OpenSearch failed for '%s': %s", clean_query, e)

        # Evaluate confidence: if no results or top result doesn't contain query terms
        query_words = [w for w in clean_query.lower().split() if len(w) > 2]
        is_low_confidence = not results or (
            bool(query_words)
            and not any(word in results[0].title.lower() for word in query_words)
        )

        # 2. Fall back to MediaWiki full-text search if no results or low confidence
        if is_low_confidence:
            try:
                ft_params = {
                    "action": "query",
                    "list": "search",
                    "srsearch": clean_query,
                    "utf8": "",
                    "srlimit": limit,
                    "format": "json",
                }
                ft_resp = await client.get(self.api_url, params=ft_params)
                ft_resp.raise_for_status()
                ft_data = ft_resp.json()
                sr_list = ft_data.get("query", {}).get("search", [])

                ft_results: list[WikiSearchResult] = []
                for item in sr_list:
                    title = item.get("title", "")
                    sec = item.get("sectiontitle")
                    raw_snippet = item.get("snippet", "")
                    clean_snippet = re.sub(r"<[^>]+>", "", raw_snippet).strip()
                    url = f"https://helldivers.wiki.gg/wiki/{title.replace(' ', '_')}"
                    if sec:
                        url += f"#{sec.replace(' ', '_')}"
                    ft_results.append(
                        WikiSearchResult(
                            title=title,
                            url=url,
                            description=clean_snippet,
                            section=sec,
                        )
                    )

                if ft_results:
                    if not results:
                        results = ft_results
                    else:
                        # Append non-duplicate full-text results
                        existing_titles = {r.title.lower() for r in results}
                        for r in ft_results:
                            if r.title.lower() not in existing_titles:
                                results.append(r)
            except Exception as ft_err:
                log.warning("Full-text search fallback failed for '%s': %s", clean_query, ft_err)

        return results

    @async_retry(max_retries=2, base_delay=2.0, backoff_factor=2.0)
    async def get_article(
        self,
        page_title: str,
        section: str | None = None,
        ttl: float = 86400.0,
    ) -> WikiArticle | None:
        """Fetch and parse a full wiki page by title.

        Uses on-disk caching with a 24-hour TTL and offloads HTML parsing to a thread pool.
        If a section anchor is specified, sets it as target_section to prioritize it in the brief.
        """
        # Check disk cache first
        cached = self._read_disk_cache(page_title, ttl=ttl)
        if cached is not None:
            if section:
                cached.target_section = section
            return cached

        client = await self._get_client()
        params = {
            "action": "parse",
            "page": page_title,
            "prop": "text|wikitext",
            "format": "json",
            "redirects": 1,
        }
        response = await client.get(self.api_url, params=params)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            log.info("MediaWiki parse error for '%s': %s", page_title, data["error"].get("info"))
            return None

        parse_obj = data.get("parse", {})
        canonical_title = parse_obj.get("title", page_title)
        html_content = parse_obj.get("text", {}).get("*", "")
        page_url = f"https://helldivers.wiki.gg/wiki/{canonical_title.replace(' ', '_')}"

        if not html_content:
            return None

        # Offload parsing to asyncio.to_thread to avoid blocking the event loop
        article = await asyncio.to_thread(clean_soup_html, html_content, canonical_title, page_url)
        if section:
            article.target_section = section

        # Cache parsed article to disk
        await asyncio.to_thread(self._write_disk_cache, article)

        return article

    @async_retry(max_retries=2, base_delay=2.0, backoff_factor=2.0)
    async def get_page_categories(self, page_title: str) -> list[str]:
        """Fetch the wiki categories for a page title.

        Queries: api.php?action=parse&page={title}&prop=categories&format=json
        Returns category names without the 'Category:' prefix,
        e.g. ['Stratagems', 'Eagle Stratagems', 'Offensive Stratagems'].
        """
        client = await self._get_client()
        params = {
            "action": "parse",
            "page": page_title,
            "prop": "categories",
            "format": "json",
            "redirects": 1,
        }
        response = await client.get(self.api_url, params=params)
        response.raise_for_status()
        data = response.json()

        if "error" in data:
            log.info("MediaWiki category fetch error for '%s': %s", page_title, data["error"].get("info"))
            return []

        parse_obj = data.get("parse", {})
        raw_cats = parse_obj.get("categories", [])
        categories: list[str] = []
        for cat in raw_cats:
            cat_name = cat.get("*", "") if isinstance(cat, dict) else str(cat)
            if cat_name:
                # MediaWiki returns underscored names; normalize to spaces
                categories.append(cat_name.replace("_", " "))
        return categories
