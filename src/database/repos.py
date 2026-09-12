"""Repository pattern — typed CRUD operations for all tables.

Provides async database access methods organized by domain concern:
- AlertRepository: Idempotent dispatch tracking.
- ProfileRepository: Helldiver profile upserts and leaderboard queries.
- MissionRepository: Mission record inserts and history queries.

See ARCHITECTURE.md §4 for the schema and §6.1 for async safety.
"""

from __future__ import annotations
