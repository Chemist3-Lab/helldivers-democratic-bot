"""Steam News API client.

Typed async HTTP client for the ISteamNews/GetNewsForApp endpoint
targeting Helldivers 2 (App ID 553850).

See ARCHITECTURE.md §7.1 for rate-limiting and caching policy.
"""

from __future__ import annotations
