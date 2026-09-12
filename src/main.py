"""Helldivers 2 Democratic Bot — Application Entrypoint.

Boot sequence:
1. Load and validate configuration from environment.
2. Configure structured logging.
3. Initialize the async SQLite database (WAL mode, create tables).
4. Create the bot instance and register all cogs.
5. Run the bot on the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from src.config import get_settings


def _setup_logging(level: str) -> None:
    """Configure root logger with a clean format."""
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


async def _run() -> None:
    """Async boot sequence."""
    settings = get_settings()
    _setup_logging(settings.log_level)
    log = logging.getLogger("helldivers")

    log.info("🦅 Initializing Helldivers Democratic Bot...")

    # ── Database ────────────────────────────────────────────────
    from src.database.engine import init_db

    await init_db(settings.database_url)
    log.info("Database initialized at %s", settings.database_url)

    # ── Bot ─────────────────────────────────────────────────────
    from src.bot.bot import create_bot

    bot = create_bot(settings)

    async with bot:
        await bot.start(settings.discord_token)


def main() -> None:
    """Synchronous entrypoint — called by the console script."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
