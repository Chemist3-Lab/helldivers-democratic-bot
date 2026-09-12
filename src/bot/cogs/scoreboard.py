"""Automated Scoreboard Debrief & MMR System Cog.

Listens on `on_message` for image attachments in `DEBRIEF_CHANNEL_ID`:
1. Validates and downloads screenshot bytes.
2. Extracts player stats via `ScoreboardVisionExtractor` (Gemini Flash).
3. Calculates Democratic Valor Rating (DVR) score for each player.
4. Commits `MissionRecord` and updates `HelldiverProfile` in SQLite.
5. Generates patriotic debrief commentary via `MinistryPersona`.
6. Replies with a structured mission debrief embed.

Includes slash commands:
- `/leaderboard [sort_by]`: View top Helldivers by total DVR or accuracy.
- `/profile [user]`: View career service record and aggregate stats.
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for mission extraction screenshots uploaded to DEBRIEF_CHANNEL_ID."""
        # 1. Ignore bot messages
        if message.author.bot:
            return

        # 2. Check channel restriction
        if message.channel.id != self.bot.settings.debrief_channel_id:
            return

        # 3. Check for image attachments
        image_attachments = [
            att for att in message.attachments
            if att.content_type and att.content_type.startswith("image/")
        ]
        if not image_attachments:
            return

        # 4. Check if message was already processed
        if await MissionRepository.is_message_processed(message.id):
            return

        # Indicate processing via typing indicator and reaction
        try:
            await message.add_reaction("🦅")
        except discord.DiscordException:
            pass

        async with message.channel.typing():
            await self._process_debrief_screenshot(message, image_attachments[0])

    async def _process_debrief_screenshot(
        self,
        message: discord.Message,
        attachment: discord.Attachment,
    ) -> None:
        """Download image, invoke Gemini vision, calculate DVR, persist, and respond."""
        try:
            image_bytes = await attachment.read()
            mime_type = attachment.content_type or "image/png"

            # Run Gemini structured extraction
            extraction: ScoreboardExtraction = await self.vision.extract_scoreboard(
                image_bytes=image_bytes,
                mime_type=mime_type,
            )

            if not extraction.players:
                await message.reply(
                    "⚠️ **Debrief Rejected:** The Ministry of Truth could not detect any readable Helldiver mission statistics in this screenshot. Ensure the full victory/extraction scoreboard is visible.",
                )
                return

            # Map the uploading Discord user or match by names
            # The uploader is linked to the first player or closest match
            submitter = message.author
            submitter_profile = await ProfileRepository.get_or_create_profile(
                discord_id=submitter.id,
                display_name=submitter.display_name,
            )

            # Calculate DVR for each player and persist
            player_results: list[tuple[PlayerExtraction, float]] = []

            for idx, p in enumerate(extraction.players):
                # Calculate DVR
                dvr_pts = calculate_mission_dvr(
                    kills=p.kills,
                    deaths=p.deaths,
                    accuracy_pct=p.accuracy_pct,
                    stims_used=p.stims_used,
                    friendly_fire_dmg=p.friendly_fire_dmg,
                    difficulty=p.difficulty or extraction.difficulty,
                )
                player_results.append((p, dvr_pts))

                # Primary submitter updates their profile with the primary row (first player or matching username)
                if idx == 0 or p.name.lower() in submitter.display_name.lower():
                    updated_prof = await ProfileRepository.record_mission_stats(
                        discord_id=submitter.id,
                        display_name=submitter.display_name,
                        kills=p.kills,
                        deaths=p.deaths,
                        stims_used=p.stims_used,
                        accuracy_pct=p.accuracy_pct,
                        friendly_fire_dmg=p.friendly_fire_dmg,
                        dvr_score=dvr_pts,
                    )
                    # Commit mission record
                    await MissionRepository.create_mission_record(
                        profile_id=updated_prof.id or 1,
                        submitted_by=submitter.id,
                        message_id=message.id,
                        kills=p.kills,
                        deaths=p.deaths,
                        stims_used=p.stims_used,
                        accuracy_pct=p.accuracy_pct,
                        friendly_fire_dmg=p.friendly_fire_dmg,
                        difficulty=p.difficulty or extraction.difficulty,
                        dvr_score=dvr_pts,
                        raw_gemini_response=extraction.model_dump_json(),
                    )

            # Generate debrief commentary for submitter/top player
            lead_player, lead_dvr = player_results[0]
            commentary = await self.persona.generate_debrief_commentary(
                player_name=submitter.display_name,
                kills=lead_player.kills,
                deaths=lead_player.deaths,
                accuracy_pct=lead_player.accuracy_pct,
                friendly_fire_dmg=lead_player.friendly_fire_dmg,
                dvr_score=lead_dvr,
                difficulty=extraction.difficulty,
            )

            # Build debrief response embed
            embed = discord.Embed(
                title=f"📋 MISSION EXTRACTION DEBRIEF — DIFFICULTY {extraction.difficulty}",
                description=f"*{commentary}*\n\n**Processed Extraction Telemetry:**",
                color=DEBRIEF_EMBED_COLOR,
            )

            for p, score in player_results:
                val = (
                    f"💀 **Kills:** {p.kills} | ⚰️ **Deaths:** {p.deaths}\n"
                    f"🎯 **Acc:** {p.accuracy_pct:.1f}% | 💉 **Stims:** {p.stims_used}\n"
                    f"⚠️ **FF Dmg:** {p.friendly_fire_dmg:.0f} | 🎖️ **DVR Points:** `+{score:.1f}`"
                )
                embed.add_field(name=f"🎖️ {p.name}", value=val, inline=False)

            embed.set_author(
                name="Ministry of Truth Automated Debrief Terminal",
                icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
            )
            embed.set_footer(text=f"Submitted by {submitter.display_name} · Points added to Super Earth Career Record")

            await message.reply(embed=embed)
            try:
                await message.add_reaction("✅")
            except discord.DiscordException:
                pass

        except Exception as e:
            log.exception("Error processing mission debrief: %s", e)
            await message.reply(
                "❌ **Terminal Error:** An anomaly occurred during image analysis. Please verify the image is uncompressed and readable.",
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
                "🎖️ **No Records Filed:** No Helldiver debrief records have been committed to the archives yet. Submit mission screenshots in the debrief channel!",
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
                "Post extraction screenshots in the debrief channel to establish a career dossier!"
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


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(ScoreboardCog(bot))
