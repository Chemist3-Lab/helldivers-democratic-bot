"""Discord Embed formatters for Debrief extraction, Leaderboard, and Helldiver Profiles."""

from __future__ import annotations

import discord

from src.ai.vision import PlayerExtraction, ScoreboardExtraction
from src.database.models import HelldiverProfile

DEBRIEF_EMBED_COLOR = 0x00FF88
LEADERBOARD_GOLD = 0xFFD700
PROFILE_PURPLE = 0x9B5DE5
PURGE_RED = 0xFF4444


def build_debrief_embed(
    extraction: ScoreboardExtraction,
    player_results: list[tuple[PlayerExtraction, float]],
    commentary: str,
    submitter_display_name: str,
) -> discord.Embed:
    """Build the official mission debrief extraction embed."""
    total_squad_kills = sum(p.kills for p in extraction.players)
    total_squad_deaths = sum(p.deaths for p in extraction.players)
    status_text = "Mission Extracted ✅" if extraction.mission_success else "Extraction Failed / MIA ⚠️"

    embed = discord.Embed(
        title="📋 MISSION EXTRACTION DEBRIEF",
        color=DEBRIEF_EMBED_COLOR,
    )

    # Mission & Squad Summary
    squad_summary = (
        f"**Operation Status:** {status_text}\n"
        f"**Squad Casualties:** `{total_squad_deaths}` Helldivers | **Total Enemies Purged:** `{total_squad_kills:,}`"
    )
    embed.add_field(name="🛡️ Mission & Squad Summary", value=squad_summary, inline=False)

    # Individual Helldiver Breakdown
    for p, score in player_results:
        score_sign = "+" if score >= 0 else ""
        val = (
            f"💀 **Kills:** {p.kills:,} | ⚰️ **Deaths:** {p.deaths}\n"
            f"🎯 **Accuracy:** {p.accuracy_pct:.1f}% | 💉 **Stims Used:** {p.stims_used}\n"
            f"⚠️ **Friendly Fire:** {p.friendly_fire_dmg:.0f} dmg | 🎖️ **Net DVR Points:** `{score_sign}{score:.1f}`"
        )
        embed.add_field(name=f"🎖️ Helldiver {p.name}", value=val, inline=False)

    # Ministry Assessment
    embed.add_field(
        name="🦅 High Command Assessment",
        value=f"*{commentary}*",
        inline=False,
    )

    embed.set_author(
        name="Super Earth Ministry of Truth Performance Audit",
        icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
    )
    embed.set_footer(
        text=f"Submitted by {submitter_display_name} · Career records committed to Super Earth Archives"
    )
    return embed


def build_leaderboard_embed(
    profiles: list[HelldiverProfile],
    by_accuracy: bool = False,
) -> discord.Embed:
    """Build the Super Earth Wall of Heroes leaderboard embed."""
    title = "🎯 TOP MARKSMEN (BY ACCURACY)" if by_accuracy else "🦅 WALL OF HEROES — TOP HELLDIVERS (BY DVR)"
    embed = discord.Embed(
        title=title,
        description="The most decorated defenders of Super Earth and Managed Democracy.\n",
        color=LEADERBOARD_GOLD,
    )

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    rows: list[str] = []
    for i, prof in enumerate(profiles):
        medal = medals[i] if i < len(medals) else f"`#{i+1}`"
        avg_acc = (prof.accuracy_sum / prof.accuracy_samples) if prof.accuracy_samples > 0 else 0.0
        if by_accuracy:
            rows.append(
                f"{medal} **{prof.display_name}** — **{avg_acc:.1f}% Acc** "
                f"({prof.total_missions} missions, {prof.dvr_current:.1f} DVR)"
            )
        else:
            rows.append(
                f"{medal} **{prof.display_name}** — **{prof.dvr_current:.1f} DVR** "
                f"({prof.total_missions} missions, {prof.total_kills} kills, {avg_acc:.1f}% acc)"
            )

    embed.add_field(name="Rankings", value="\n".join(rows), inline=False)
    embed.set_footer(text="Higher difficulty and team play yields greater Democratic Valor.")
    return embed


def calculate_dvr_rank(dvr_current: float) -> str:
    """Determine Patriotic Military Rank based on dvr_current."""
    if dvr_current >= 600:
        return "SUPREME MARSHAL"
    if dvr_current >= 550:
        return "HIGH MARSHAL"
    if dvr_current >= 500:
        return "MAJOR MARSHAL"
    if dvr_current >= 450:
        return "BRIGADIER MARSHAL"
    if dvr_current >= 400:
        return "COLONEL HELLDIVER"
    if dvr_current >= 350:
        return "LIEUTENANT COLONEL HELLDIVER"
    if dvr_current >= 300:
        return "MAJOR HELLDIVER"
    if dvr_current >= 260:
        return "CAPTAIN HELLDIVER"
    if dvr_current >= 230:
        return "FIRST LIEUTENANT"
    if dvr_current >= 200:
        return "SECOND LIEUTENANT"
    if dvr_current >= 175:
        return "COMMAND SERGEANT HELLDIVER"
    if dvr_current >= 150:
        return "MASTER SERGEANT HELLDIVER"
    if dvr_current >= 125:
        return "GUNNERY SERGEANT HELLDIVER"
    if dvr_current >= 100:
        return "STAFF SERGEANT HELLDIVER"
    if dvr_current >= 75:
        return "SERGEANT HELLDIVER"
    if dvr_current >= 50:
        return "CORPORAL HELLDIVER"
    if dvr_current >= 25:
        return "LANCE HELLDIVER"
    return "LINE HELLDIVER"


def build_profile_embed(
    prof: HelldiverProfile,
    target: discord.User | discord.Member,
) -> discord.Embed:
    """Build the career dossier service record embed for a Helldiver."""
    avg_acc = (prof.accuracy_sum / prof.accuracy_samples) if prof.accuracy_samples > 0 else 0.0
    kd_ratio = prof.total_kills / max(prof.total_deaths, 1)
    rank = calculate_dvr_rank(prof.dvr_current)

    embed = discord.Embed(
        title=f"🎖️ CAREER DOSSIER: {prof.display_name}",
        description=f"**Current Status:** 🎖️ {rank}\n**Rolling Democratic Valor Rating (DVR):** `{prof.dvr_current:.1f}`",
        color=PROFILE_PURPLE,
    )
    embed.set_thumbnail(url=target.display_avatar.url)

    embed.add_field(name="🚀 Extractions", value=str(prof.total_missions), inline=True)
    embed.add_field(name="💀 Total Kills", value=f"{prof.total_kills:,}", inline=True)
    embed.add_field(name="⚰️ Casualties", value=str(prof.total_deaths), inline=True)

    embed.add_field(name="🎯 Average Accuracy", value=f"{avg_acc:.1f}%", inline=True)
    embed.add_field(name="⚔️ Kill/Death Ratio", value=f"{kd_ratio:.2f}", inline=True)
    embed.add_field(name="💉 Stims Injected", value=str(prof.total_stims_used), inline=True)

    embed.add_field(name="⚠️ Friendly Fire Damage", value=f"{prof.total_friendly_fire:,.0f}", inline=True)
    embed.add_field(name="🌟 Cumulative DVR Points", value=f"{prof.dvr_total:,.1f}", inline=True)

    embed.set_footer(text="Official Super Earth Military Personnel Registry · Freedom Forever")
    return embed


def build_purge_embed(author_name: str) -> discord.Embed:
    """Build the archive purge confirmation embed."""
    embed = discord.Embed(
        title="🧹 Ministry Archive Purge Complete",
        description="*🧹 Ministry Archive Purge Complete: All Helldiver dossiers and combat debrief records have been reset to zero.*",
        color=PURGE_RED,
    )
    embed.set_author(
        name="Super Earth Ministry of Truth Archives",
        icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
    )
    embed.set_footer(
        text=f"Purge authorized by {author_name} · All dossiers expunged"
    )
    return embed
