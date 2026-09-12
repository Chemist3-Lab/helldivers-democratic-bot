"""Wiki scraper and chunker for helldivers.wiki.gg.

Scrapes tactical data pages, chunks content for embedding,
and caches results to disk (JSON) with a 24-hour TTL.

See ARCHITECTURE.md §7.1 for caching policy.
"""

from __future__ import annotations
