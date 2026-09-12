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
    """Result of a search query on the MediaWiki instance."""

    title: str
    url: str
    description: str = Field(default="")
    section: str | None = Field(default=None, description="Matched section anchor if found in full-text search.")


class WikiArticle(BaseModel):
    """Processed wiki article ready for tactical terminal consumption."""

    title: str
    url: str
    summary: str
    content: str
    quote: str = Field(default="", description="In-game tactical description quote if available.")
    infobox: dict[str, str] = Field(default_factory=dict)
    sections: dict[str, str] = Field(default_factory=dict)
    target_section: str | None = Field(default=None, description="Prioritized section anchor from search query.")
    fetched_at: float = Field(default_factory=time.time)

    def get_tactical_brief(self, target_section: str | None = None, max_chars: int = 30000) -> str:
        """Combine infobox and clean sections into a structured tactical brief.

        If target_section is provided (or set on the article), that section and its
        adjacent tables are prioritized at the top of the dossier.
        """
        active_target = target_section or self.target_section
        lines = [f"# Tactical Dossier: {self.title}", f"Source: {self.url}\n"]
        if self.summary:
            lines.append(f"## Executive Summary\n{self.summary}\n")

        if self.infobox:
            lines.append("## Specifications & Technical Data")
            for k, v in self.infobox.items():
                lines.append(f"- **{k}**: {v}")
            lines.append("")

        prioritized_sections: list[tuple[str, str]] = []
        remaining_sections: list[tuple[str, str]] = []

        for sec_title, sec_content in self.sections.items():
            if not sec_content.strip():
                continue
            if active_target and (
                active_target.lower() in sec_title.lower()
                or sec_title.lower() in active_target.lower()
            ):
                prioritized_sections.append((sec_title, sec_content.strip()))
            else:
                remaining_sections.append((sec_title, sec_content.strip()))

        # Prioritized section(s) appear first
        for sec_title, sec_content in prioritized_sections:
            lines.append(f"## {sec_title} [TARGETED INTEL]\n{sec_content}\n")

        # Remaining sections in standard wiki order
        for sec_title, sec_content in remaining_sections:
            lines.append(f"## {sec_title}\n{sec_content}\n")

        full = "\n".join(lines)
        return full[:max_chars]

    @property
    def full_tactical_brief(self) -> str:
        """Combine infobox and clean sections into a structured tactical brief."""
        return self.get_tactical_brief()


# ─── Parser Helper (Runs in asyncio.to_thread) ────────────────────────────────


def _format_table_markdown(table: BeautifulSoup) -> list[str]:
    """Format an HTML table (wikitable, infobox, stats-table) into structured markdown key-value pairs.

    Handles header extraction, footnote stripping, and formats cells cleanly:
    e.g., 'Part Name: Head | Health: 1,500 | AV: Heavy | Location: Front Side | Fatal?: Yes'
    """
    rows = table.find_all("tr")
    if not rows:
        return []

    headers: list[str] = []
    header_row = table.find("tr")
    if header_row:
        for th in header_row.find_all(["th", "td"]):
            h_text = th.get_text(" ", strip=True)
            # Remove bracketed footnotes and trailing footnote numbers (e.g. 'Health 1' -> 'Health')
            h_text = h_text.split("[")[0].strip()
            h_text = re.sub(r"\s+\d+$", "", h_text)
            headers.append(h_text)

    output_lines: list[str] = []
    # If the first row had headers, skip it for data rows
    data_rows = rows[1:] if any(row.find_all("th") for row in rows[:1]) else rows

    for row in data_rows:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if not cells or all(not c for c in cells):
            continue

        row_pairs: list[str] = []
        for idx, cell in enumerate(cells):
            if not cell:
                continue
            header_name = headers[idx] if idx < len(headers) and headers[idx] else f"Col_{idx+1}"
            row_pairs.append(f"{header_name}: {cell}")

        if row_pairs:
            output_lines.append(f"| {' | '.join(row_pairs)} |")

    return output_lines


def _clean_soup_html(html_text: str, page_title: str, page_url: str) -> WikiArticle:
    """Parse raw MediaWiki HTML into a structured WikiArticle using BeautifulSoup."""
    soup = BeautifulSoup(html_text, "html.parser")

    # Remove script, style, nav, edit links, references, table of contents (toc)
    for tag in soup(["script", "style", "noscript", ".mw-editsection", ".navbox", ".reference", "#toc", ".toc"]):
        tag.decompose()

    # Remove version and patch notice boxes (e.g. "Last updated: 1.006.300 Current patch: 1.007.002")
    for box in soup.find_all(["div", "table"]):
        classes = box.get("class") or []
        if any("druid" in c for c in classes):
            continue
        txt = box.get_text().lower().strip()
        if (txt.startswith("last updated:") or "current patch:" in txt) and len(txt) < 200:
            box.decompose()

    # Extract in-game tactical description quote if present (e.g. from blockquote)
    quote_text = ""
    quote_elem = soup.find("blockquote")
    if quote_elem:
        for p in quote_elem.find_all("p"):
            pt = p.get_text(" ", strip=True)
            if pt and not pt.startswith("—") and not pt.startswith("-") and len(pt) > 10:
                quote_text = pt
                break

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

    # Also parse druid-style infoboxes (helldivers.wiki.gg uses druid-infobox, not portable-infobox)
    druid_infobox = soup.find(class_=re.compile(r"druid-infobox|druid-container"))
    if druid_infobox:
        _ARROW_MAP = {"Right": "➡", "Up": "⬆", "Down": "⬇", "Left": "⬅"}
        for row in druid_infobox.find_all(class_=re.compile(r"druid-row")):
            label_el = row.find(class_=re.compile(r"druid-label"))
            data_el = row.find(class_=re.compile(r"druid-data"))
            if not label_el or not data_el:
                continue
            key = label_el.get_text(strip=True)
            if not key:
                continue
            # Special handling for stratagem arrow codes (images with no text)
            arrow_imgs = data_el.find_all("img", alt=re.compile(r"Stratagem.?Arrow", re.IGNORECASE))
            if arrow_imgs:
                arrows: list[str] = []
                for img in arrow_imgs:
                    alt = img.get("alt", "")
                    for direction, symbol in _ARROW_MAP.items():
                        if direction.lower() in alt.lower():
                            arrows.append(symbol)
                            break
                val = " ".join(arrows) if arrows else data_el.get_text(" ", strip=True)
            else:
                val = data_el.get_text(" ", strip=True)
            if val:
                infobox_data[key] = val

    # Also extract standard wikitable infobox tables (th/td key-value rows)
    if not infobox_data:
        for tbl in soup.find_all("table", class_=re.compile(r"infobox")):
            for row in tbl.find_all("tr"):
                th = row.find("th")
                td = row.find("td")
                if th and td:
                    key = th.get_text(strip=True)
                    # Handle arrow images in td
                    arrow_imgs = td.find_all("img", alt=re.compile(r"Stratagem.?Arrow|Arrow", re.IGNORECASE))
                    if arrow_imgs and not td.get_text(strip=True):
                        _ARROW_MAP_FALLBACK = {"Right": "➡", "Up": "⬆", "Down": "⬇", "Left": "⬅"}
                        arrows_fb: list[str] = []
                        for img in arrow_imgs:
                            alt = img.get("alt", "")
                            for d, s in _ARROW_MAP_FALLBACK.items():
                                if d.lower() in alt.lower():
                                    arrows_fb.append(s)
                                    break
                        val = " ".join(arrows_fb) if arrows_fb else ""
                    else:
                        val = td.get_text(" ", strip=True)
                    if key and val:
                        infobox_data[key] = val

    # Extract Call-in Time, Uses, and Cooldown from Stratagem Statistics or general wikitables
    for tbl in soup.find_all("table", class_=re.compile(r"wikitable")):
        for tr in tbl.find_all("tr"):
            th = tr.find("th")
            tds = tr.find_all("td")
            if th and tds:
                th_text = th.get_text(strip=True).lower()
                td_val = tds[0].get_text(" ", strip=True)
                if "call-in" in th_text and "Call-in Time" not in infobox_data:
                    infobox_data["Call-in Time"] = td_val
                elif th_text == "uses" and "Uses" not in infobox_data:
                    infobox_data["Uses"] = td_val
                elif "cooldown" in th_text and "Cooldown" not in infobox_data:
                    # In Stratagem Statistics, row may have td[0]='Standard', td[1]='180 seconds'
                    if len(tds) > 1 and any(char.isdigit() for char in tds[1].get_text()):
                        infobox_data["Cooldown"] = tds[1].get_text(" ", strip=True)
                    else:
                        infobox_data["Cooldown"] = td_val

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

    # Extract sections (including paragraphs, lists, card containers, and data tables)
    sections: dict[str, str] = {}
    current_section = "Overview"
    current_lines: list[str] = []

    for tag in content_div.find_all(["h2", "h3", "h4", "p", "ul", "ol", "table", "div"]):
        if tag.name in ("h2", "h3", "h4"):
            if current_lines:
                sections[current_section] = "\n".join(current_lines)
                current_lines = []
            clean_heading = re.sub(r"\[edit.*?\]", "", tag.get_text(strip=True)).strip()
            current_section = clean_heading
            # Ignore standard wiki boilerplate / non-tactical sections
            if current_section.lower() in (
                "references", "see also", "external links", "navigation", "gallery", "trivia", "change history"
            ):
                current_section = ""
        elif current_section:
            if tag.name == "p":
                t = tag.get_text(" ", strip=True)
                if t:
                    current_lines.append(t)
            elif tag.name in ("ul", "ol"):
                # Avoid processing nested list items multiple times
                if tag.find_parent(["ul", "ol"]):
                    continue
                for li in tag.find_all("li", recursive=False):
                    li_t = li.get_text(" ", strip=True)
                    if li_t:
                        current_lines.append(f"- {li_t}")
            elif tag.name == "table":
                # Avoid processing nested tables multiple times
                if tag.find_parent("table"):
                    continue
                table_classes = tag.get("class", [])
                is_data_table = any(
                    cls in ("wikitable", "infobox", "stats-table", "enemy-attack-stats")
                    for cls in table_classes
                ) or tag.find("th") is not None
                if is_data_table:
                    tbl_lines = _format_table_markdown(tag)
                    if tbl_lines:
                        current_lines.append("\n[Tactical Data Table:]")
                        current_lines.extend(tbl_lines)
                        current_lines.append("")
            elif tag.name == "div":
                # Skip known noise containers that produce duplicate text
                div_classes = set(tag.get("class", []))
                noise_classes = {
                    "breadcrumb", "noexcerpt", "navbox", "ranger-navbox",
                    "toc", "tabs", "tab", "tab-spacer", "druid-infobox",
                    "druid-container", "mw-collapsible", "navigation-not-searchable",
                    "flextablediv",
                }
                if div_classes & noise_classes:
                    continue
                # Only handle card containers, link grids, or text divs that don't contain sub-blocks
                if tag.find_parent("table") or tag.find_parent(class_="Roundededges"):
                    continue
                if tag.find_parent(class_=re.compile(r"druid-infobox|druid-container|breadcrumb|navbox|tabs")):
                    continue
                if tag.find(["h2", "h3", "h4", "table", "p", "ul", "ol"]):
                    continue
                links = [
                    a.get_text(strip=True)
                    for a in tag.find_all("a")
                    if a.get_text(strip=True)
                ]
                links = [
                    l
                    for l in links
                    if not l.startswith("[") and not l.isdigit() and len(l) > 1
                ]
                if links:
                    for item in links:
                        if f"- {item}" not in current_lines:
                            current_lines.append(f"- {item}")
                else:
                    t = tag.get_text(" ", strip=True)
                    if t and len(t) > 3 and t not in current_lines:
                        current_lines.append(t)

    if current_section and current_lines:
        sections[current_section] = "\n".join(current_lines)

    # Clean text content
    clean_content = content_div.get_text("\n", strip=True)
    # Strip version tags (e.g. 1.004.000, 1.007.002) from summary
    summary = re.sub(r"\b\d+\.\d{3}\.\d{3}\b", "", summary).strip()

    return WikiArticle(
        title=page_title,
        url=page_url,
        summary=summary,
        quote=quote_text,
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
        article = await asyncio.to_thread(_clean_soup_html, html_content, canonical_title, page_url)
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
