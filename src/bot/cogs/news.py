"""Official Intel & Patch Notes Cog.

Polls the Steam News API for Helldivers 2 (App ID 553850) on a task loop
and broadcasts patches, hotfixes, and Warbond updates to the alert channel.

See ARCHITECTURE.md §3.1 for the complete data flow specification.
"""

from __future__ import annotations

from discord.ext import commands


class NewsCog(commands.Cog, name="Steam News"):
    """Polls Steam News API and dispatches news alerts."""

    ...


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NewsCog(bot))  # type: ignore[arg-type]
