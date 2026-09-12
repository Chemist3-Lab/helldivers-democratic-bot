"""Discord Bot subclass with setup_hook for cog loading.

Implements:
- Custom Bot subclass with typed settings injection.
- setup_hook() to register all cogs on startup.
- Intents configuration (message content, guild messages, members).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from src.config import Settings

log = logging.getLogger(__name__)

# Cog extensions to load on startup (dotted module paths)
EXTENSIONS: list[str] = [
    "src.bot.cogs.war",
    "src.bot.cogs.news",
    "src.bot.cogs.wiki",
    "src.bot.cogs.scoreboard",
]


class HelldiversBot(commands.Bot):
    """Custom Bot subclass with application settings and typed references."""

    settings: Settings

    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix="!",  # Slash commands are primary; prefix is fallback
            intents=intents,
        )
        self.settings = settings

    async def setup_hook(self) -> None:
        """Load all cog extensions and sync slash commands."""
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info("Loaded extension: %s", ext)
            except Exception:
                log.exception("Failed to load extension: %s", ext)

        # Sync slash commands to the target guild for instant availability
        guild = discord.Object(id=self.settings.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced to guild %s", self.settings.guild_id)

    async def on_ready(self) -> None:
        """Log successful connection."""
        assert self.user is not None
        log.info("🦅 %s is online — spreading Managed Democracy!", self.user)


def create_bot(settings: Settings) -> HelldiversBot:
    """Factory function to create a configured bot instance."""
    return HelldiversBot(settings)
