"""MediaWiki API Client and parser for helldivers.wiki.gg.

Endpoints & Operations:
- Uses MediaWiki Action API at `https://helldivers.wiki.gg/api.php`
- `action=opensearch`: Search for wiki articles by keyword
- `action=parse`: Fetch parsed wikitext/HTML or plain wikitext
- Parses acquisition costs, weapon stats, stratagem call-in times, and enemy data
- Extracts clean markdown/text content suited for RAG embeddings and LLM context
- Persistent disk caching with a 24-hour TTL

Uses asyncio.to_thread for CPU-bound parsing of large HTML documents.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
import httpx

from src.services._retry import async_retry

log = logging.getLogger(__name__)

MEDIAWIKI_API_URL = "https://helldivers.wiki.gg/api.php"
CACHE_DIR = Path("wiki_chunks")


# ─── Data Models ─────────────────────────────────────────────────────────────


class WikiSearchResult(BaseModel):
    """Result of an OpenSearch query on the MediaWiki instance."""

    title: str
    url: str
    description: str = Field(default="")


class WikiArticle(BaseModel):
    """Processed wiki article ready for tactical terminal consumption."""

    title: str
    url: str
    summary: str
    content: str
    infobox: dict[str, str] = Field(default_factory=dict)
    sections: dict[str, str] = Field(default_factory=dict)
    fetched_at: float = Field(default_factory=time.time)

    @property
    def full_tactical_brief(self) -> str:
        """Combine infobox and clean sections into a structured tactical brief."""
        lines = [f"# Tactical Dossier: {self.title}", f"Source: {self.url}\n"]
        if self.summary:
            lines.append(f"## Executive Summary\n{self.summary}\n")

        if self.infobox:
            lines.append("## Specifications & Technical Data")
            for k, v in self.infobox.items():
                lines.append(f"- **{k}**: {v}")
            lines.append("")

        for sec_title, sec_content in self.sections.items():
            if sec_content.strip():
                lines.append(f"## {sec_title}\n{sec_content.strip()}\n")

        return "\n".join(lines)


# ─── Parser Helper (Runs in asyncio.to_thread) ────────────────────────────────


def _clean_soup_html(html_text: str, page_title: str, page_url: str) -> WikiArticle:
    """Parse raw MediaWiki HTML into a structured WikiArticle using BeautifulSoup."""
    soup = BeautifulSoup(html_text, "html.parser")

    # Remove script, style, nav, edit links, references
    for tag in soup(["script", "style", "noscript", ".mw-editsection", ".navbox", ".reference"]):
        tag.decompose()

    infobox_data: dict[str, str] = {}
    infobox = soup.find(class_=re.compile(r"portable-infobox|infobox"))
    if infobox:
        # Extract key-value rows
        for row in infobox.find_all("tr"):
            th = row.find(["th", "td"], class_=re.compile(r"pi-data-label|label"))
            td = row.find(["td"], class_=re.compile(r"pi-data-value|value"))
            if th and td:
                key = th.get_text(strip=True)
                val = td.get_text(" ", strip=True)
                if key and val:
                    infobox_data[key] = val

        # Handle portable infobox items
        for item in infobox.find_all(class_="pi-item"):
            label = item.find(class_="pi-data-label")
            val = item.find(class_="pi-data-value")
            if label and val:
                key = label.get_text(strip=True)
                v = val.get_text(" ", strip=True)
                if key and v:
                    infobox_data[key] = v

    # Extract summary (paragraphs before first heading)
    summary_paras: list[str] = []
    content_div = soup.find(class_="mw-parser-output") or soup
    for elem in content_div.children:
        if elem.name in ("h2", "h3", "table"):
            break
        if elem.name == "p":
            text = elem.get_text(" ", strip=True)
            if text:
                summary_paras.append(text)
    summary = "\n\n".join(summary_paras)

    # Extract sections
    sections: dict[str, str] = {}
    current_section = "Overview"
    current_lines: list[str] = []

    for tag in content_div.find_all(["h2", "h3", "p", "ul", "ol"]):
        if tag.name in ("h2", "h3"):
            if current_lines:
                sections[current_section] = "\n".join(current_lines)
                current_lines = []
            current_section = tag.get_text(strip=True).replace("[edit]", "").strip()
            # Ignore standard wiki boilerplate sections
            if current_section.lower() in ("references", "see also", "external links", "navigation"):
                current_section = ""
        elif current_section:
            if tag.name == "p":
                t = tag.get_text(" ", strip=True)
                if t:
                    current_lines.append(t)
            elif tag.name in ("ul", "ol"):
                for li in tag.find_all("li", recursive=False):
                    li_t = li.get_text(" ", strip=True)
                    if li_t:
                        current_lines.append(f"- {li_t}")

    if current_section and current_lines:
        sections[current_section] = "\n".join(current_lines)

    # Clean text content
    clean_content = content_div.get_text("\n", strip=True)

    return WikiArticle(
        title=page_title,
        url=page_url,
        summary=summary,
        content=clean_content,
        infobox=infobox_data,
        sections=sections,
    )


# ─── Service Client ──────────────────────────────────────────────────────────


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
        """Search the wiki using OpenSearch API."""
        client = await self._get_client()
        params = {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "namespace": 0,
            "format": "json",
        }
        response = await client.get(self.api_url, params=params)
        response.raise_for_status()
        data = response.json()

        # OpenSearch format: [query, [titles], [descriptions], [urls]]
        if not isinstance(data, list) or len(data) < 4:
            return []

        titles: list[str] = data[1]
        descriptions: list[str] = data[2]
        urls: list[str] = data[3]

        results: list[WikiSearchResult] = []
        for i, title in enumerate(titles):
            desc = descriptions[i] if i < len(descriptions) else ""
            url = urls[i] if i < len(urls) else f"https://helldivers.wiki.gg/wiki/{title.replace(' ', '_')}"
            results.append(WikiSearchResult(title=title, url=url, description=desc))

        return results

    @async_retry(max_retries=2, base_delay=2.0, backoff_factor=2.0)
    async def get_article(self, page_title: str, ttl: float = 86400.0) -> WikiArticle | None:
        """Fetch and parse a full wiki page by title.

        Uses on-disk caching with a 24-hour TTL and offloads HTML parsing to a thread pool.
        """
        # Check disk cache first
        cached = self._read_disk_cache(page_title, ttl=ttl)
        if cached is not None:
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
        article = await asyncio.to_thread(_clean_soup_html, html_content, canonical_title, page_url)

        # Cache parsed article to disk
        await asyncio.to_thread(self._write_disk_cache, article)

        return article
