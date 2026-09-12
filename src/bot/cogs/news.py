"""Official Intel & Patch Notes Cog.

Polls the Steam News API for Helldivers 2 (App ID 553850) on a task loop,
deduplicates against DispatchedAlerts in the database, and broadcasts patches,
hotfixes, and Warbond updates to the configured alert channel.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import discord
from discord.ext import commands, tasks

from src.database.repos import AlertRepository
from src.services.steam_api import SteamApiClient, SteamNewsItem

if TYPE_CHECKING:
    from src.bot.bot import HelldiversBot

log = logging.getLogger(__name__)

STEAM_BLUE = 0x1B2838
PATCH_GREEN = 0x2A9D8F


class NewsCog(commands.Cog, name="Steam News"):
    """Polls Steam News API and dispatches patch and update alerts."""

    def __init__(self, bot: HelldiversBot) -> None:
        self.bot = bot
        self.api = SteamApiClient()
        self.poll_steam_news.change_interval(seconds=self.bot.settings.news_poll_seconds)
        self.poll_steam_news.start()

    async def cog_unload(self) -> None:
        """Cancel background tasks and clean up client sessions."""
        self.poll_steam_news.cancel()
        await self.api.close()

    @tasks.loop(seconds=600)
    async def poll_steam_news(self) -> None:
        """Continuous task loop polling for Steam patches and updates."""
        try:
            await self._check_news()
        except Exception:
            log.exception("Steam News poll cycle encountered an error; retrying next cycle")

    @poll_steam_news.before_loop
    async def before_poll(self) -> None:
        """Wait until Discord client is ready."""
        await self.bot.wait_until_ready()

    async def _get_alert_channel(self) -> discord.TextChannel | None:
        """Retrieve the configured alert channel object."""
        channel = self.bot.get_channel(self.bot.settings.alert_channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.bot.settings.alert_channel_id)
            except Exception:
                log.warning("Could not fetch alert channel %s", self.bot.settings.alert_channel_id)
                return None
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def _check_news(self) -> None:
        """Fetch latest patches/hotfixes from Steam, deduplicate, and broadcast."""
        news_items = await self.api.get_latest_news(count=5, ttl=300.0, only_patches=True)
        if not news_items:
            return

        channel = await self._get_alert_channel()
        if not channel:
            return

        # Broadcast in chronological order (oldest first)
        for item in reversed(news_items):
            ext_id = item.gid
            already_sent = await AlertRepository.is_dispatched("steam_news", ext_id)
            if already_sent:
                continue

            embed = self._build_news_embed(item)
            role_mention = f"<@&{self.bot.settings.helldiver_role_id}>"
            await channel.send(
                content=f"🛠️ **NEW HELLDIVERS 2 INTEL DETECTED** {role_mention}",
                embed=embed,
            )
            await AlertRepository.mark_dispatched("steam_news", ext_id, title=item.title)
            log.info("Dispatched Steam patch notification '%s' (GID: %s)", item.title, item.gid)

    def _build_news_embed(self, item: SteamNewsItem) -> discord.Embed:
        """Construct a formatted Discord embed for a Steam patch or article."""
        # Trim contents to fit within embed character limits
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


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(NewsCog(bot))
