"""Galactic War & Order Dispatcher Cog.

Polls the Helldivers 2 community API on a task loop and dispatches
idempotent rich embeds for Major Orders, Dispatches, and high-priority
campaign defenses to the configured alert channel.

See ARCHITECTURE.md §3.1 for the complete data flow specification.
"""

from __future__ import annotations

from discord.ext import commands


class WarCog(commands.Cog, name="Galactic War"):
    """Polls Helldivers API and dispatches war alerts."""

    ...


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarCog(bot))  # type: ignore[arg-type]
