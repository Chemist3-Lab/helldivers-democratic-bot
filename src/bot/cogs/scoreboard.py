"""Automated Scoreboard Debrief & MMR System Cog.

Listens for mission extraction screenshots in the debrief channel,
sends images to Gemini Flash for structured data extraction, validates
via Pydantic, calculates DVR scores, and persists to SQLite.

Provides `/leaderboard` and `/profile` slash commands.

See ARCHITECTURE.md §3.2 and §5 for data flow and DVR formula.
"""

from __future__ import annotations

from discord.ext import commands


class ScoreboardCog(commands.Cog, name="Scoreboard"):
    """Mission debrief image extraction and leaderboard system."""

    ...


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScoreboardCog(bot))  # type: ignore[arg-type]
