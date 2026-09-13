"""Discord Embed formatters and objective resolvers for Galactic War and Major Orders."""

from __future__ import annotations

import logging
import re
import discord

from src.services.hd2_api import Campaign, Dispatch, MajorOrder

log = logging.getLogger(__name__)

SUPER_EARTH_YELLOW = 0xFFDE00
DEFENSE_RED = 0xE63946
MAJOR_ORDER_CYAN = 0x00F0FF


def build_dispatch_embed(item: Dispatch) -> discord.Embed:
    """Build formatted embed for a High Command dispatch alert."""
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
    return embed


def build_major_order_alert_embed(
    order: MajorOrder,
    fallback_dispatch: str | None = None,
) -> discord.Embed:
    """Build formatted embed for an automated Major Order broadcast alert."""
    brief_text = order.brief
    if not brief_text or brief_text == "No briefing provided.":
        if fallback_dispatch:
            brief_text = fallback_dispatch
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
    return embed


def resolve_major_order_objectives(
    order: MajorOrder,
    planets_map: dict[int, str],
    campaigns: list[Campaign],
) -> list[str]:
    """Resolve target planets, defense status, and liberation percentages for a Major Order."""
    objectives: list[str] = []
    try:
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
                status_icon = "✅" if prog == 1 else "⏳"
                status_text = "Completed" if prog == 1 else "In Progress"
                objectives.append(f"{status_icon} Directive #{idx+1} — {status_text}")

        # Fallback: If tasks list yielded no objectives, scan briefing text for uppercase planet names
        if not objectives and planets_map:
            brief_upper = f"{order.brief} {order.task_description}".upper()
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

    except Exception as exc:
        log.warning("Failed to resolve Major Order target planets: %s", exc)

    return objectives


def build_war_status_embed(
    campaigns: list[Campaign],
    orders: list[MajorOrder],
    fallback_dispatch: str | None = None,
) -> discord.Embed:
    """Build embed for the /war slash command."""
    embed = discord.Embed(
        title="🌌 SUPER EARTH GALACTIC WAR STATUS",
        description="Live strategic telemetry from Super Earth High Command.",
        color=SUPER_EARTH_YELLOW,
    )

    # Active Major Order summary
    if orders:
        active_order = orders[0]
        brief_text = active_order.brief
        if not brief_text or brief_text == "No briefing provided.":
            if fallback_dispatch:
                brief_text = fallback_dispatch
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

    # Active liberation targets (exclude defense campaigns and 100% liberated with 0 effective health)
    liberation_campaigns = []
    for c in campaigns:
        if c.is_defense:
            continue
        lib = c.planet.liberation_pct
        eff_health = c.planet.effective_health
        if lib >= 100.0 and (eff_health is not None and eff_health <= 0):
            continue
        liberation_campaigns.append(c)

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
    return embed


def build_major_order_command_embed(
    order: MajorOrder | None,
    objectives: list[str],
    fallback_dispatch: str | None = None,
) -> discord.Embed:
    """Build embed for the /major_order slash command."""
    if not order:
        embed = discord.Embed(
            title="🎖️ SUPER EARTH HIGH COMMAND DIRECTIVE",
            description="No active Major Order currently registered in High Command archives.\n\nAll Helldivers are instructed to conduct localized liberation and defense actions until the next strategic offensive is broadcast.",
            color=MAJOR_ORDER_CYAN,
        )
        embed.set_footer(text="Stand by for High Command transmissions · Dive for Democracy")
        return embed

    brief_text = order.brief
    if not brief_text or brief_text == "No briefing provided.":
        if fallback_dispatch:
            brief_text = fallback_dispatch
        else:
            brief_text = order.task_description or "Mobilize immediately for Super Earth!"

    title_text = order.title
    if not title_text or title_text == "MAJOR ORDER":
        title_text = f"Major Order #{order.id}"

    embed = discord.Embed(
        title=f"🎖️ {title_text}",
        description=brief_text,
        color=MAJOR_ORDER_CYAN,
    )
    embed.set_author(
        name="Super Earth High Command Strategic Assignment",
        icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
    )

    if objectives:
        embed.add_field(
            name="🎯 Target Planets & Objectives",
            value="\n".join(objectives),
            inline=False,
        )
    elif order.task_description and order.task_description != brief_text:
        embed.add_field(name="📋 Directives", value=order.task_description, inline=False)

    embed.add_field(
        name="⏳ Time Remaining",
        value=order.time_remaining_discord,
        inline=True,
    )

    medals = order.medal_reward
    reward_str = f"🏅 **{medals} Medals**" if medals > 0 else "🎖️ High Command Commendation"
    embed.add_field(name="🎁 Reward", value=reward_str, inline=True)

    embed.set_footer(text="Failure is not an option · Managed Democracy Prevails")
    return embed
