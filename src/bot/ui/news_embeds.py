"""Discord Embed formatters for Steam news and official patch announcements."""

from __future__ import annotations

import discord

from src.services.steam_api import SteamNewsItem

STEAM_BLUE = 0x1B2838
PATCH_GREEN = 0x2A9D8F


def build_news_embed(item: SteamNewsItem) -> discord.Embed:
    """Construct a formatted Discord embed for a Steam patch or article."""
    contents = item.clean_contents
    if len(contents) > 1000:
        contents = contents[:997] + "..."

    embed = discord.Embed(
        title=f"📜 {item.title}",
        url=item.url,
        description=contents or "Click the title link above to read the full Steam announcement.",
        color=PATCH_GREEN if item.is_patch_or_update else STEAM_BLUE,
    )

    embed.set_author(
        name=f"Arrowhead Game Studios / Steam ({item.feedlabel or 'Official'})",
        url=item.url,
        icon_url="https://store.steampowered.com/favicon.ico",
    )

    if item.author:
        embed.add_field(name="Author", value=item.author, inline=True)

    embed.set_footer(text="Official Super Earth Tactical Ordinance & Patch Log")
    return embed
