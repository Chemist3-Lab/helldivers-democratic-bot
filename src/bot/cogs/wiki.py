"""Ministry of Truth Tactical Terminal / Wiki RAG Cog.

Provides the `/wiki <query>` slash command that retrieves tactical
data from pre-embedded wiki chunks and answers using Gemini Flash
with the Ministry of Truth patriotic persona.

See ARCHITECTURE.md §3.3 and §8 for data flow and persona specification.
"""

from __future__ import annotations

from discord.ext import commands


class WikiCog(commands.Cog, name="Ministry of Truth"):
    """Wiki RAG slash command for tactical queries."""

    ...


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WikiCog(bot))  # type: ignore[arg-type]
