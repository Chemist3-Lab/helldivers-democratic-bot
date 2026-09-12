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
import re
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

        # Check existing alerts to dispatch in chronological order
        for item in reversed(dispatches[:5]):
            ext_id = str(item.id)
            already_sent = await AlertRepository.is_dispatched("war_dispatch", ext_id)
            if already_sent:
                continue

            clean_text = item.clean_message or "A new dispatch has arrived from Super Earth High Command."
            embed = discord.Embed(
                title=f"📡 HIGH COMMAND DISPATCH #{item.id}",
                description=clean_text,
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

            brief_text = order.brief
            if not brief_text or brief_text == "No briefing provided.":
                dispatch_text = await self.api.get_latest_major_order_dispatch()
                if dispatch_text:
                    brief_text = dispatch_text
                else:
                    brief_text = order.task_description or "Mobilize immediately for Super Earth!"

            embed = discord.Embed(
                title=f"🎖️ NEW MAJOR ORDER: {order.title}",
                description=brief_text,
                color=MAJOR_ORDER_CYAN,
            )
            embed.set_author(
                name="Super Earth High Command Directive",
                icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
            )
            if order.task_description:
                embed.add_field(name="📋 Directives", value=order.task_description, inline=False)
            countdown = order.time_remaining_discord
            if countdown != "Pending High Command Update":
                embed.add_field(name="⏳ Time Remaining", value=countdown, inline=True)
            elif order.expires_in > 0:
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

        # Active Major Order summary with dispatch fallback
        if orders:
            active_order = orders[0]
            brief_text = active_order.brief
            if not brief_text or brief_text == "No briefing provided.":
                dispatch_text = await self.api.get_latest_major_order_dispatch()
                if dispatch_text:
                    brief_text = dispatch_text
                else:
                    brief_text = active_order.task_description or "Mobilize immediately for Super Earth!"

            title_text = active_order.title
            if title_text == "MAJOR ORDER" or not title_text:
                title_text = f"Major Order #{active_order.id}"

            embed.add_field(
                name=f"🎖️ {title_text}",
                value=f"{brief_text[:400]}..." if len(brief_text) > 400 else brief_text,
                inline=False,
            )

        # Distinguish defense campaigns vs active liberation offensives
        defense_campaigns = [c for c in campaigns if c.is_defense]

        # Active liberation targets:
        # Exclude defense campaigns, and exclude planets that are 100% liberated with 0 enemy health
        liberation_campaigns = []
        for c in campaigns:
            if c.is_defense:
                continue
            lib = c.planet.liberation_pct
            eff_health = c.planet.effective_health
            if lib >= 100.0 and (eff_health is not None and eff_health <= 0):
                continue
            liberation_campaigns.append(c)

        # Sort active campaigns by planet.players descending so most contested fronts appear at top
        liberation_campaigns.sort(key=lambda c: c.players, reverse=True)

        if defense_campaigns:
            defense_campaigns.sort(key=lambda c: c.players, reverse=True)
            defense_text = []
            for c in defense_campaigns[:5]:
                planet = c.planet
                def_pct = planet.defense_health_pct
                players_note = f" · {c.players:,} Helldivers" if c.players > 0 else ""
                defense_text.append(f"🛡️ **{planet.name}** ({planet.sector} Sector) — Defense Health: {def_pct:.1f}%{players_note}")
            embed.add_field(name="🚨 PRIORITY PLANETARY DEFENSES", value="\n".join(defense_text), inline=False)

        if liberation_campaigns:
            offensive_text = []
            for c in liberation_campaigns[:6]:
                planet = c.planet
                lib_pct = planet.liberation_pct
                players_note = f" · {c.players:,} Helldivers" if c.players > 0 else ""
                offensive_text.append(f"⚔️ **{planet.name}** ({planet.sector} Sector) — Liberation: **{lib_pct:.1f}%**{players_note}")
            embed.add_field(name="🎯 Active Liberation Offensives", value="\n".join(offensive_text), inline=False)
        elif not defense_campaigns:
            embed.add_field(
                name="🕊️ Galactic Front Stable",
                value="No active combat campaigns registered. All sectors report 100% Super Earth control.",
                inline=False,
            )

        embed.set_footer(text="Dive for Democracy · Super Earth Ministry of Defense")
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
            embed = discord.Embed(
                title="🎖️ SUPER EARTH HIGH COMMAND DIRECTIVE",
                description="No active Major Order currently registered in High Command archives.\n\nAll Helldivers are instructed to conduct localized liberation and defense actions until the next strategic offensive is broadcast.",
                color=MAJOR_ORDER_CYAN,
            )
            embed.set_footer(text="Stand by for High Command transmissions · Dive for Democracy")
            await interaction.followup.send(embed=embed)
            return

        order = orders[0]
        brief_text = order.brief
        if not brief_text or brief_text == "No briefing provided.":
            dispatch_text = await self.api.get_latest_major_order_dispatch()
            if dispatch_text:
                brief_text = dispatch_text
            else:
                brief_text = order.task_description or "Mobilize immediately for Super Earth!"

        title_text = order.title
        if not title_text or title_text == "MAJOR ORDER":
            title_text = f"Major Order #{order.id}"

        # ── 1. Resolve Target Planets & Live Task Tracking ──
        objectives: list[str] = []
        try:
            planets_map = await self.api.get_planets_map()
            campaigns = await self.api.get_campaigns()
            camp_map = {c.planet.index: c for c in campaigns}
            camp_name_map = {c.planet.name.upper(): c for c in campaigns}

            tasks = order.tasks or order.setting.get("tasks", [])
            for idx, task in enumerate(tasks):
                prog = order.progress[idx] if idx < len(order.progress) else 0
                vals = task.get("values", [])
                val_types = task.get("valueTypes", [])

                # In Arrowhead API, valueType 12 denotes Planet Index
                planet_idx = None
                for vt, v in zip(val_types, vals):
                    if vt == 12:
                        planet_idx = v
                        break
                if planet_idx is None and vals:
                    # Fallback: inspect raw values against known planet indices
                    for v in reversed(vals):
                        if v in planets_map:
                            planet_idx = v
                            break

                if planet_idx is not None and planet_idx in planets_map:
                    planet_name = planets_map[planet_idx]
                    camp = camp_map.get(planet_idx) or camp_name_map.get(planet_name.upper())
                    if prog == 1:
                        objectives.append(f"✅ **{planet_name}** — Liberated")
                    else:
                        if camp and camp.is_defense:
                            def_pct = camp.planet.defense_health_pct
                            objectives.append(f"🛡️ **{planet_name}** — Defense in Progress ({def_pct:.1f}%)")
                        else:
                            lib_pct = camp.planet.liberation_pct if camp else 0.0
                            objectives.append(f"⏳ **{planet_name}** — In Progress ({lib_pct:.1f}%)")
                else:
                    # Non-planetary objective
                    status_icon = "✅" if prog == 1 else "⏳"
                    status_text = "Completed" if prog == 1 else "In Progress"
                    objectives.append(f"{status_icon} Directive #{idx+1} — {status_text}")

            # Fallback: If tasks list yielded no objectives, scan briefing text for uppercase planet names
            if not objectives and planets_map:
                brief_upper = f"{brief_text} {order.task_description}".upper()
                for p_idx, p_name in planets_map.items():
                    if len(p_name) > 3 and re.search(r"\b" + re.escape(p_name.upper()) + r"\b", brief_upper):
                        camp = camp_map.get(p_idx) or camp_name_map.get(p_name.upper())
                        lib_pct = camp.planet.liberation_pct if camp else 0.0
                        if lib_pct >= 100.0:
                            objectives.append(f"✅ **{p_name}** — Liberated")
                        elif camp and camp.is_defense:
                            def_pct = camp.planet.defense_health_pct
                            objectives.append(f"🛡️ **{p_name}** — Defense in Progress ({def_pct:.1f}%)")
                        else:
                            objectives.append(f"⏳ **{p_name}** — In Progress ({lib_pct:.1f}%)")

        except Exception as obj_err:
            log.warning("Failed to resolve Major Order target planets: %s", obj_err)

        embed = discord.Embed(
            title=f"🎖️ {title_text}",
            description=brief_text,
            color=MAJOR_ORDER_CYAN,
        )
        embed.set_author(
            name="Super Earth High Command Strategic Assignment",
            icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
        )

        # ── Objectives / Directives ──
        if objectives:
            embed.add_field(
                name="🎯 Target Planets & Objectives",
                value="\n".join(objectives),
                inline=False,
            )
        elif order.task_description and order.task_description != brief_text:
            embed.add_field(name="📋 Directives", value=order.task_description, inline=False)

        # ── 2. Dynamic Live Countdown ──
        embed.add_field(
            name="⏳ Time Remaining",
            value=order.time_remaining_discord,
            inline=True,
        )

        # ── Reward ──
        medals = order.medal_reward
        reward_str = f"🏅 **{medals} Medals**" if medals > 0 else "🎖️ High Command Commendation"
        embed.add_field(name="🎁 Reward", value=reward_str, inline=True)

        embed.set_footer(text="Failure is not an option · Managed Democracy Prevails")
        await interaction.followup.send(embed=embed)


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(WarCog(bot))
