"""Automated Scoreboard Debrief & MMR System Cog.

Provides slash commands:
- `/debrief <image> [difficulty]`: Submit an end-of-mission extraction scoreboard for telemetry parsing and DVR scoring.
- `/leaderboard [sort_by]`: View top Helldivers by total DVR or accuracy.
- `/profile [user]`: View career service record and aggregate stats.
- `/reset_leaderboard <confirm>`: Administrative purge of all Helldiver dossiers and records.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands

from src.ai.persona import MinistryPersona
from src.ai.vision import PlayerExtraction, ScoreboardExtraction, ScoreboardVisionExtractor
from src.bot.ui.scoreboard_embeds import (
    DEBRIEF_EMBED_COLOR,
    LEADERBOARD_GOLD,
    PROFILE_PURPLE,
    PURGE_RED,
    build_debrief_embed,
    build_leaderboard_embed,
    build_profile_embed,
    build_purge_embed,
)
from src.database.models import HelldiverProfile
from src.database.repos import MissionRepository, ProfileRepository
from src.services.dvr import calculate_mission_dvr

if TYPE_CHECKING:
    from src.bot.bot import HelldiversBot

log = logging.getLogger(__name__)

# Re-export embed title and colors for tests and backward compatibility
# Title: '📋 MISSION EXTRACTION DEBRIEF"'
DEBRIEF_TITLE = '📋 MISSION EXTRACTION DEBRIEF"'

__all__ = [
    "DEBRIEF_EMBED_COLOR",
    "LEADERBOARD_GOLD",
    "PROFILE_PURPLE",
    "PURGE_RED",
    "ScoreboardCog",
    "setup",
]


def _resolve_deterministic_id(name: str) -> int:
    """Generate a deterministic synthetic Discord snowflake for recurring non-Discord players.

    Uses MD5 rather than process-salted hash() so IDs remain consistent across bot restarts.
    """
    digest = hashlib.md5(f"helldiver_{name.lower()}".encode("utf-8")).hexdigest()
    return int(digest[:14], 16) % (10**15) + 10**16


def _match_player_identity(
    p_name: str,
    submitter: discord.User | discord.Member,
    guild: discord.Guild | None,
    is_first_and_only: bool,
) -> tuple[int, str]:
    """Resolve a player's Discord ID and display name from guild members or synthetic ID."""
    is_submitter = (
        p_name.lower() in submitter.display_name.lower()
        or submitter.display_name.lower() in p_name.lower()
        or is_first_and_only
    )
    if is_submitter:
        return submitter.id, submitter.display_name

    if guild:
        for member in guild.members:
            if (
                member.name.lower() == p_name.lower()
                or member.display_name.lower() == p_name.lower()
            ):
                return member.id, member.display_name

    return _resolve_deterministic_id(p_name), p_name


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
                dvr_pts = calculate_mission_dvr(
                    kills=p.kills,
                    deaths=p.deaths,
                    accuracy_pct=p.accuracy_pct,
                    stims_used=p.stims_used,
                    friendly_fire_dmg=p.friendly_fire_dmg,
                    difficulty=diff_val,
                )
                player_results.append((p, dvr_pts))

                target_id, target_name = _match_player_identity(
                    p_name=p.name,
                    submitter=submitter,
                    guild=interaction.guild,
                    is_first_and_only=(idx == 0 and len(extraction.players) == 1),
                )

                # Update or create HelldiverProfile
                updated_prof = await ProfileRepository.record_mission_stats(
                    discord_id=target_id,
                    display_name=target_name,
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

            embed = build_debrief_embed(
                extraction=extraction,
                player_results=player_results,
                commentary=commentary,
                submitter_display_name=submitter.display_name,
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

        embed = build_leaderboard_embed(profiles, by_accuracy=by_accuracy)
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

        embed = build_profile_embed(prof, target)
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
        is_admin = getattr(getattr(interaction.user, "guild_permissions", None), "administrator", False)
        if not is_admin:
            await interaction.response.send_message(
                "🚫 **Access Denied:** Only Super Earth Server Administrators possess clearance to purge Ministry archives.",
                ephemeral=True,
            )
            return

        if not confirm:
            await interaction.response.send_message(
                "⚠️ **Purge Aborted:** Ministry Archive purge requires explicit confirmation (`confirm=True`). Archives remain intact.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            await ProfileRepository.purge_all_records()
            embed = build_purge_embed(interaction.user.display_name)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            log.exception("Error executing archive purge: %s", e)
            await interaction.followup.send(
                "❌ **Terminal Error:** Failed to purge archives due to an unexpected database error.",
                ephemeral=True,
            )


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(ScoreboardCog(bot))
