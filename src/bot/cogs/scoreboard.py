"""Automated Scoreboard Debrief & MMR System Cog.

Provides slash commands:
- `/debrief <image> [difficulty]`: Submit an end-of-mission extraction scoreboard for telemetry parsing and DVR scoring.
- `/leaderboard [sort_by]`: View top Helldivers by total DVR or accuracy.
- `/profile [user]`: View career service record and aggregate stats.
- `/reset_leaderboard <confirm>`: Administrative purge of all Helldiver dossiers and records.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands

from src.ai.persona import MinistryPersona
from src.ai.vision import PlayerExtraction, ScoreboardExtraction, ScoreboardVisionExtractor
from src.database.models import HelldiverProfile
from src.database.repos import MissionRepository, ProfileRepository
from src.services.dvr import calculate_mission_dvr

if TYPE_CHECKING:
    from src.bot.bot import HelldiversBot

log = logging.getLogger(__name__)

DEBRIEF_EMBED_COLOR = 0x00FF88
LEADERBOARD_GOLD = 0xFFD700
PROFILE_PURPLE = 0x9B5DE5
PURGE_RED = 0xFF4444


class ScoreboardCog(commands.Cog, name="Scoreboard"):
    """Mission debrief image extraction and leaderboard system."""

    def __init__(self, bot: HelldiversBot) -> None:
        self.bot = bot
        self.vision = ScoreboardVisionExtractor(
            api_key=self.bot.settings.gemini_api_key,
            model_name=self.bot.settings.gemini_model,
        )
        self.persona = MinistryPersona(
            api_key=self.bot.settings.gemini_api_key,
            model_name=self.bot.settings.gemini_model,
        )

    @app_commands.command(
        name="debrief",
        description="Submit a post-mission scoreboard screenshot for Ministry performance audit and DVR calculation.",
    )
    @app_commands.describe(
        image="Screenshot of the mission extraction scoreboard",
        difficulty="Optional mission difficulty level (1-10)",
    )
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="1 - Trivial", value=1),
        app_commands.Choice(name="2 - Easy", value=2),
        app_commands.Choice(name="3 - Medium", value=3),
        app_commands.Choice(name="4 - Challenging", value=4),
        app_commands.Choice(name="5 - Hard", value=5),
        app_commands.Choice(name="6 - Extreme", value=6),
        app_commands.Choice(name="7 - Suicide Mission", value=7),
        app_commands.Choice(name="8 - Impossible", value=8),
        app_commands.Choice(name="9 - Helldive", value=9),
        app_commands.Choice(name="10 - Super Helldive", value=10),
    ])
    async def debrief(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        difficulty: app_commands.Choice[int] | None = None,
    ) -> None:
        """Process mission extraction scoreboard screenshot, calculate DVR, and log career progress."""
        # 1. Channel Guard: verify command executed in authorized helldiver channel
        auth_channel_id = self.bot.settings.helldiver_channel_id
        if interaction.channel_id != auth_channel_id:
            await interaction.response.send_message(
                f"⚠️ **Unauthorized Tactical Channel:** Mission debriefs must be submitted in the authorized tactical channel (<#{auth_channel_id}>).",
                ephemeral=True,
            )
            return

        # 2. Attachment Validation
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message(
                "⚠️ Invalid tactical payload. Please attach a valid image file.",
                ephemeral=True,
            )
            return

        # 3. Immediate Deferral to prevent interaction timeout while Gemini processes
        await interaction.response.defer(thinking=True)

        # 4. Check for duplicate processing by attachment ID
        if await MissionRepository.is_message_processed(image.id):
            await interaction.followup.send(
                "📋 **Duplicate Extraction:** This mission scoreboard has already been processed into the Super Earth Archives.",
                ephemeral=True,
            )
            return

        try:
            # 5. Byte Streaming & Extraction
            image_bytes = await image.read()
            mime_type = image.content_type or "image/png"

            # Run Gemini structured vision extraction (names + stats only)
            extraction: ScoreboardExtraction = await self.vision.extract_scoreboard(
                image_bytes=image_bytes,
                mime_type=mime_type,
            )

            if not extraction.players:
                await interaction.followup.send(
                    "⚠️ **Debrief Rejected:** The Ministry of Truth could not detect any readable Helldiver mission statistics in this screenshot. Ensure the full victory/extraction scoreboard is visible.",
                )
                return

            submitter = interaction.user
            diff_val: int | None = difficulty.value if difficulty is not None else None

            # 6. Data Processing & Storage for each player row
            player_results: list[tuple[PlayerExtraction, float]] = []

            for idx, p in enumerate(extraction.players):
                # Calculate DVR (defaults to 1.0 multiplier if difficulty is None)
                dvr_pts = calculate_mission_dvr(
                    kills=p.kills,
                    deaths=p.deaths,
                    accuracy_pct=p.accuracy_pct,
                    stims_used=p.stims_used,
                    friendly_fire_dmg=p.friendly_fire_dmg,
                    difficulty=diff_val,
                )
                player_results.append((p, dvr_pts))

                # Match player identity to Discord user
                is_submitter = (
                    p.name.lower() in submitter.display_name.lower()
                    or submitter.display_name.lower() in p.name.lower()
                    or (idx == 0 and len(extraction.players) == 1)
                )

                target_discord_id: int | None = submitter.id if is_submitter else None
                target_display_name = submitter.display_name if is_submitter else p.name

                if not target_discord_id and interaction.guild:
                    for member in interaction.guild.members:
                        if (
                            member.name.lower() == p.name.lower()
                            or member.display_name.lower() == p.name.lower()
                        ):
                            target_discord_id = member.id
                            target_display_name = member.display_name
                            break

                if not target_discord_id:
                    target_discord_id = int(abs(hash(f"helldiver_{p.name.lower()}")) % (10**15) + 10**16)

                # Update or create HelldiverProfile
                updated_prof = await ProfileRepository.record_mission_stats(
                    discord_id=target_discord_id,
                    display_name=target_display_name,
                    kills=p.kills,
                    deaths=p.deaths,
                    stims_used=p.stims_used,
                    accuracy_pct=p.accuracy_pct,
                    friendly_fire_dmg=p.friendly_fire_dmg,
                    dvr_score=dvr_pts,
                )

                # Commit MissionRecord
                await MissionRepository.create_mission_record(
                    profile_id=updated_prof.id or 1,
                    submitted_by=submitter.id,
                    message_id=image.id,
                    kills=p.kills,
                    deaths=p.deaths,
                    stims_used=p.stims_used,
                    accuracy_pct=p.accuracy_pct,
                    friendly_fire_dmg=p.friendly_fire_dmg,
                    difficulty=diff_val if diff_val is not None else 1,
                    dvr_score=dvr_pts,
                    raw_gemini_response=extraction.model_dump_json(),
                )

            # 7. Debrief Commentary & Rich Output Embed
            lead_player, lead_dvr = player_results[0]
            commentary = await self.persona.generate_debrief_commentary(
                player_name=submitter.display_name,
                kills=lead_player.kills,
                deaths=lead_player.deaths,
                accuracy_pct=lead_player.accuracy_pct,
                friendly_fire_dmg=lead_player.friendly_fire_dmg,
                dvr_score=lead_dvr,
                difficulty=diff_val,
            )

            total_squad_kills = sum(p.kills for p in extraction.players)
            total_squad_deaths = sum(p.deaths for p in extraction.players)
            status_text = "Mission Extracted ✅" if extraction.mission_success else "Extraction Failed / MIA ⚠️"

            embed = discord.Embed(
                title="📋 MISSION EXTRACTION DEBRIEF",
                color=DEBRIEF_EMBED_COLOR,
            )

            # Mission & Squad Summary (difficulty removed from header)
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
                text=f"Submitted by {submitter.display_name} · Career records committed to Super Earth Archives"
            )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            log.exception("Error processing mission debrief: %s", e)
            await interaction.followup.send(
                "❌ **Terminal Error:** An anomaly occurred during image analysis. Please verify the image is uncompressed and readable.",
                ephemeral=True,
            )

    @app_commands.command(name="leaderboard", description="View top Helldivers ranked by Democratic Valor Rating (DVR).")
    @app_commands.describe(sort_by="Criterion to rank Helldivers")
    @app_commands.choices(sort_by=[
        app_commands.Choice(name="Democratic Valor Rating (DVR)", value="dvr"),
        app_commands.Choice(name="Average Shot Accuracy", value="accuracy"),
    ])
    async def leaderboard(self, interaction: discord.Interaction, sort_by: str = "dvr") -> None:
        """Display the Super Earth Wall of Heroes leaderboard."""
        await interaction.response.defer(thinking=True)

        by_accuracy = (sort_by == "accuracy")
        profiles = await ProfileRepository.get_leaderboard(limit=10, by_accuracy=by_accuracy)

        if not profiles:
            await interaction.followup.send(
                "🎖️ **No Records Filed:** No Helldiver debrief records have been committed to the archives yet. Submit mission screenshots with `/debrief`!",
            )
            return

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
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="profile", description="View a Helldiver's career service record and Democratic Valor Rating.")
    @app_commands.describe(user="The Helldiver whose service record you wish to review (defaults to yourself)")
    async def profile(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        """Display an individual Helldiver's career statistics."""
        await interaction.response.defer(thinking=True)

        target = user or interaction.user
        prof: HelldiverProfile | None = await ProfileRepository.get_by_discord_id(target.id)

        if prof is None or prof.total_missions == 0:
            msg = (
                f"📋 **No Service Record Found:** Helldiver **{target.display_name}** has not submitted any mission debriefs. "
                "Use `/debrief` with an extraction scoreboard screenshot to establish a career dossier!"
            )
            await interaction.followup.send(msg)
            return

        avg_acc = (prof.accuracy_sum / prof.accuracy_samples) if prof.accuracy_samples > 0 else 0.0
        kd_ratio = (prof.total_kills / max(prof.total_deaths, 1))

        # Determine Patriotic Rank/Title based on dvr_current
        if prof.dvr_current >= 300:
            rank = "⭐⭐⭐⭐⭐ Super Citizen"
        elif prof.dvr_current >= 200:
            rank = "⭐⭐⭐⭐ Elite Helldiver"
        elif prof.dvr_current >= 120:
            rank = "⭐⭐⭐ Veteran Helldiver"
        elif prof.dvr_current >= 50:
            rank = "⭐⭐ Helldiver"
        else:
            rank = "⭐ Cadet"

        embed = discord.Embed(
            title=f"🎖️ CAREER DOSSIER: {prof.display_name}",
            description=f"**Current Status:** {rank}\n**Rolling Democratic Valor Rating (DVR):** `{prof.dvr_current:.1f}`",
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
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="reset_leaderboard",
        description="Purge all Helldiver dossiers and combat debrief records (Admin only).",
    )
    @app_commands.describe(confirm="Confirm complete archive wipe (True to execute)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def reset_leaderboard(self, interaction: discord.Interaction, confirm: bool) -> None:
        """Administrative purge of all career profiles and mission records."""
        # 1. Administrator Permission Guard
        is_admin = getattr(getattr(interaction.user, "guild_permissions", None), "administrator", False)
        if not is_admin:
            await interaction.response.send_message(
                "🚫 **Access Denied:** Only Super Earth Server Administrators possess clearance to purge Ministry archives.",
                ephemeral=True,
            )
            return

        # 2. Confirmation Check
        if not confirm:
            await interaction.response.send_message(
                "⚠️ **Purge Aborted:** Ministry Archive purge requires explicit confirmation (`confirm=True`). Archives remain intact.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            await ProfileRepository.purge_all_records()

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
                text=f"Purge authorized by {interaction.user.display_name} · All dossiers expunged"
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.exception("Error executing archive purge: %s", e)
            await interaction.followup.send(
                "❌ **Terminal Error:** Failed to purge archives due to an unexpected database error.",
                ephemeral=True,
            )


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(ScoreboardCog(bot))

