"""Galactic War & Order Dispatcher Cog.

Polls the Helldivers 2 community API on a task loop and dispatches
idempotent rich embeds for Major Orders, Dispatches, and high-priority
campaign defenses to the configured alert channel.

Includes slash commands:
- `/war`: View active Galactic War campaigns and planetary defense priorities.
- `/major_order`: View active Major Order planet objectives, directives, remaining time, and medal reward.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.bot.ui.war_embeds import (
    DEFENSE_RED,
    MAJOR_ORDER_CYAN,
    SUPER_EARTH_YELLOW,
    build_dispatch_embed,
    build_major_order_alert_embed,
    build_major_order_command_embed,
    build_war_status_embed,
    resolve_major_order_objectives,
)
from src.database.repos import AlertRepository
from src.services.hd2_api import Campaign, Dispatch, HD2ApiClient, MajorOrder

if TYPE_CHECKING:
    from src.bot.bot import HelldiversBot

log = logging.getLogger(__name__)

__all__ = [
    "DEFENSE_RED",
    "MAJOR_ORDER_CYAN",
    "SUPER_EARTH_YELLOW",
    "WarCog",
    "setup",
]


class WarCog(commands.Cog, name="Galactic War"):
    """Polls Helldivers API and dispatches war alerts."""

    def __init__(self, bot: HelldiversBot) -> None:
        self.bot = bot
        self.api = HD2ApiClient(contact=self.bot.settings.hd2_contact)
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
        """Retrieve the configured Helldiver broadcast channel object."""
        channel_id = self.bot.settings.helldiver_channel_id
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                log.warning("Could not fetch Helldiver broadcast channel %s", channel_id)
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

        for item in reversed(dispatches[:5]):
            ext_id = str(item.id)
            if await AlertRepository.is_dispatched("war_dispatch", ext_id):
                continue

            embed = build_dispatch_embed(item)
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
            if await AlertRepository.is_dispatched("war_major_order", ext_id):
                continue

            fallback_dispatch = None
            if not order.brief or order.brief == "No briefing provided.":
                fallback_dispatch = await self.api.get_latest_major_order_dispatch()

            embed = build_major_order_alert_embed(order, fallback_dispatch=fallback_dispatch)
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

        fallback_dispatch = None
        if orders and (not orders[0].brief or orders[0].brief == "No briefing provided."):
            fallback_dispatch = await self.api.get_latest_major_order_dispatch()

        embed = build_war_status_embed(
            campaigns=campaigns,
            orders=orders,
            fallback_dispatch=fallback_dispatch,
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="major_order",
        description="View the current Galactic War Major Order, target planets, dynamic countdown, and medal reward.",
    )
    async def major_order(self, interaction: discord.Interaction) -> None:
        """Query and display the active Super Earth High Command Major Order."""
        await interaction.response.defer(thinking=True)

        try:
            orders = await self.api.get_major_orders()
        except Exception as e:
            log.error("Failed to retrieve Major Order: %s", e)
            await interaction.followup.send(
                "❌ **Communications Interrupted:** Unable to reach High Command Major Order registry. Re-establishing link...",
                ephemeral=True,
            )
            return

        if not orders:
            embed = build_major_order_command_embed(order=None, objectives=[])
            await interaction.followup.send(embed=embed)
            return

        order = orders[0]
        objectives: list[str] = []
        try:
            planets_map = await self.api.get_planets_map()
            campaigns = await self.api.get_campaigns()
            objectives = resolve_major_order_objectives(order, planets_map, campaigns)
        except Exception as obj_err:
            log.warning("Failed to resolve Major Order target planets: %s", obj_err)

        fallback_dispatch = None
        if not order.brief or order.brief == "No briefing provided.":
            fallback_dispatch = await self.api.get_latest_major_order_dispatch()

        embed = build_major_order_command_embed(
            order=order,
            objectives=objectives,
            fallback_dispatch=fallback_dispatch,
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(WarCog(bot))
