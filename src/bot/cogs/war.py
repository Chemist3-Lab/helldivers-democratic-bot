"""Galactic War & Order Dispatcher Cog.

Polls the Helldivers 2 community API on a task loop and dispatches
idempotent rich embeds for Major Orders, Dispatches, and high-priority
campaign defenses to the configured alert channel.

Includes the `/war` slash command for on-demand Galactic War briefings.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.database.repos import AlertRepository
from src.services.hd2_api import Campaign, Dispatch, HD2ApiClient, MajorOrder

if TYPE_CHECKING:
    from src.bot.bot import HelldiversBot

log = logging.getLogger(__name__)

SUPER_EARTH_YELLOW = 0xFFDE00
DEFENSE_RED = 0xE63946
MAJOR_ORDER_CYAN = 0x00F0FF


class WarCog(commands.Cog, name="Galactic War"):
    """Polls Helldivers API and dispatches war alerts."""

    def __init__(self, bot: HelldiversBot) -> None:
        self.bot = bot
        self.api = HD2ApiClient()
        self.poll_war_status.change_interval(seconds=self.bot.settings.war_poll_seconds)
        self.poll_war_status.start()

    async def cog_unload(self) -> None:
        """Cancel background tasks and clean up HTTP sessions."""
        self.poll_war_status.cancel()
        await self.api.close()

    @tasks.loop(seconds=300)
    async def poll_war_status(self) -> None:
        """Continuous task loop polling for Major Orders and Dispatches."""
        try:
            await self._check_dispatches()
            await self._check_major_orders()
        except Exception:
            log.exception("Galactic War poll cycle encountered an error; retrying next cycle")

    @poll_war_status.before_loop
    async def before_poll(self) -> None:
        """Wait until the Discord bot gateway is ready."""
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

    async def _check_dispatches(self) -> None:
        """Fetch latest dispatches, deduplicate with DispatchedAlerts, and post alerts."""
        dispatches = await self.api.get_dispatches(ttl=120.0)
        if not dispatches:
            return

        channel = await self._get_alert_channel()
        if not channel:
            return

        # Check existing alerts to dispatch in chronological order
        for item in reversed(dispatches[:5]):
            ext_id = str(item.id)
            already_sent = await AlertRepository.is_dispatched("war_dispatch", ext_id)
            if already_sent:
                continue

            embed = discord.Embed(
                title=f"📡 HIGH COMMAND DISPATCH #{item.id}",
                description=item.message or "A new dispatch has arrived from Super Earth High Command.",
                color=SUPER_EARTH_YELLOW,
            )
            embed.set_author(
                name="Super Earth Ministry of Defense",
                icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
            )
            embed.set_footer(text="Managed Democracy · Galactic War Terminal")

            role_mention = f"<@&{self.bot.settings.helldiver_role_id}>"
            await channel.send(content=f"⚠️ **ATTENTION ALL HELLDIVERS** {role_mention}", embed=embed)
            await AlertRepository.mark_dispatched("war_dispatch", ext_id, title=f"Dispatch #{item.id}")
            log.info("Dispatched new war dispatch #%s", item.id)

    async def _check_major_orders(self) -> None:
        """Fetch Major Orders, deduplicate, and post alerts."""
        orders = await self.api.get_major_orders(ttl=240.0)
        if not orders:
            return

        channel = await self._get_alert_channel()
        if not channel:
            return

        for order in orders:
            ext_id = str(order.id)
            already_sent = await AlertRepository.is_dispatched("war_major_order", ext_id)
            if already_sent:
                continue

            embed = discord.Embed(
                title=f"🎖️ NEW MAJOR ORDER: {order.title}",
                description=order.brief or order.task_description or "Mobilize immediately for Super Earth!",
                color=MAJOR_ORDER_CYAN,
            )
            embed.set_author(
                name="Super Earth High Command Directive",
                icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
            )
            if order.task_description:
                embed.add_field(name="📋 Directives", value=order.task_description, inline=False)
            if order.expires_in > 0:
                hours = order.expires_in // 3600
                embed.add_field(name="⏳ Time Remaining", value=f"~{hours} Hours", inline=True)

            embed.set_footer(text="Failure is not an option. For Super Earth!")

            role_mention = f"<@&{self.bot.settings.helldiver_role_id}>"
            await channel.send(content=f"🚨 **NEW MAJOR ORDER ISSUED** {role_mention}", embed=embed)
            await AlertRepository.mark_dispatched("war_major_order", ext_id, title=order.title)
            log.info("Dispatched new Major Order #%s: %s", order.id, order.title)

    @app_commands.command(name="war", description="View active Galactic War campaigns and planetary defense priorities.")
    async def war_status(self, interaction: discord.Interaction) -> None:
        """Display current planetary campaigns, defense events, and liberation status."""
        await interaction.response.defer(thinking=True)

        try:
            campaigns = await self.api.get_campaigns()
            orders = await self.api.get_major_orders()
        except Exception as e:
            log.error("Failed to retrieve war status: %s", e)
            await interaction.followup.send(
                "❌ **Communications Interrupted:** Unable to link with the Galactic War Terminal. Re-establishing connection...",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🌌 SUPER EARTH GALACTIC WAR STATUS",
            description="Live strategic telemetry from Super Earth High Command.",
            color=SUPER_EARTH_YELLOW,
        )

        # Active Major Order summary
        if orders:
            active_order = orders[0]
            embed.add_field(
                name=f"🎖️ Major Order: {active_order.title}",
                value=f"{active_order.brief[:250]}..." if len(active_order.brief) > 250 else active_order.brief,
                inline=False,
            )

        # Highlight defense campaigns vs attack campaigns
        defense_campaigns = [c for c in campaigns if c.is_defense]
        other_campaigns = [c for c in campaigns if not c.is_defense][:6]

        if defense_campaigns:
            defense_text = []
            for c in defense_campaigns[:5]:
                planet = c.planet
                health_pct = (planet.current_health / planet.max_health * 100) if planet.max_health > 0 else 0
                defense_text.append(f"🛡️ **{planet.name}** ({planet.sector} Sector) — Health: {health_pct:.1f}%")
            embed.add_field(name="🚨 PRIORITY PLANETARY DEFENSES", value="\n".join(defense_text), inline=False)

        if other_campaigns:
            offensive_text = []
            for c in other_campaigns:
                planet = c.planet
                lib_pct = (100.0 - (planet.current_health / planet.max_health * 100)) if planet.max_health > 0 else 0
                offensive_text.append(f"⚔️ **{planet.name}** — Liberation: {max(lib_pct, 0.0):.1f}%")
            embed.add_field(name="🎯 Active Liberation Campaigns", value="\n".join(offensive_text), inline=False)

        embed.set_footer(text="Dive for Democracy · Super Earth Ministry of Defense")
        await interaction.followup.send(embed=embed)


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(WarCog(bot))
