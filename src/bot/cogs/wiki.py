"""Ministry of Truth Tactical Terminal / Wiki RAG Cog.

Provides slash commands:
- `/stratagem <query>` or `/stats <query>`: Query weapon, stratagem, and enemy stats from WikiClient.
- `/ask_ministry <question>`: Query the Ministry of Truth AI terminal with a 15-second per-user cooldown.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands

from src.ai.persona import MinistryPersona
from src.services.wiki_client import WikiArticle, WikiClient

if TYPE_CHECKING:
    from src.bot.bot import HelldiversBot

log = logging.getLogger(__name__)

SUPER_EARTH_GOLD = 0xFFD700
TERMINAL_BLUE = 0x00A8FF


class WikiCog(commands.Cog, name="Ministry of Truth"):
    """Wiki RAG and Ministry of Truth Tactical Terminal commands."""

    def __init__(self, bot: HelldiversBot) -> None:
        self.bot = bot
        self.wiki = WikiClient()
        self.persona = MinistryPersona(
            api_key=self.bot.settings.gemini_api_key,
            model_name=self.bot.settings.gemini_model,
        )

    async def cog_unload(self) -> None:
        """Cleanup WikiClient session."""
        await self.wiki.close()

    @app_commands.command(
        name="stratagem",
        description="Look up tactical specifications, weapon statistics, or stratagem call-in data.",
    )
    @app_commands.describe(query="Name of weapon, stratagem, enemy, or armor (e.g., 'Quasar Cannon', 'Charger', '500kg')")
    async def stratagem(self, interaction: discord.Interaction, query: str) -> None:
        """Search helldivers.wiki.gg and display structured tactical data."""
        await interaction.response.defer(thinking=True)

        try:
            results = await self.wiki.search(query, limit=3)
            if not results:
                await interaction.followup.send(
                    f"🔍 **No Official Dossier Found:** No matching intelligence on `{query}` was located in the Super Earth Archives.",
                    ephemeral=True,
                )
                return

            best_match = results[0]
            article = await self.wiki.get_article(best_match.title)

            if not article:
                await interaction.followup.send(
                    f"⚠️ **Data Corrupted:** Unable to parse dossier for **{best_match.title}**. Visit: {best_match.url}",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title=f"📖 TACTICAL DOSSIER: {article.title}",
                url=article.url,
                description=article.summary[:1000] if article.summary else "Official tactical report filed in archives.",
                color=SUPER_EARTH_GOLD,
            )

            # Add infobox statistics (e.g. Damage, Fire Rate, Penetration, Call-in time)
            if article.infobox:
                # Filter down to the most critical tactical rows (up to 8 fields)
                count = 0
                for k, v in article.infobox.items():
                    if count >= 8:
                        break
                    # Keep field values clean and brief
                    val_str = v[:150]
                    embed.add_field(name=k, value=val_str, inline=True)
                    count += 1

            embed.set_author(
                name="Super Earth Ministry of Truth Archives",
                icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
            )
            embed.set_footer(text="Data source: helldivers.wiki.gg · Verified by High Command")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            log.exception("Error fulfilling /stratagem query for '%s'", query)
            await interaction.followup.send("❌ Error contacting the Super Earth archives. Please retry shortly.", ephemeral=True)

    @app_commands.command(
        name="stats",
        description="Alias for /stratagem — retrieve tactical weapon or enemy stats.",
    )
    @app_commands.describe(query="Name of weapon, enemy, stratagem, or booster")
    async def stats(self, interaction: discord.Interaction, query: str) -> None:
        """Alias forwarder for stratagem command."""
        await self.stratagem.callback(self, interaction, query)  # type: ignore[misc]

    @app_commands.command(
        name="ask_ministry",
        description="Consult the Ministry of Truth Tactical Terminal with any Helldivers question.",
    )
    @app_commands.describe(question="Your tactical inquiry for the Ministry of Truth")
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: (i.guild_id, i.user.id))
    async def ask_ministry(self, interaction: discord.Interaction, question: str) -> None:
        """Invoke the Ministry of Truth persona with optional wiki context."""
        await interaction.response.defer(thinking=True)

        try:
            # 1. Look up any relevant wiki context to ground the response in real stats
            search_results = await self.wiki.search(question, limit=1)
            context = ""
            if search_results:
                matched_article = await self.wiki.get_article(search_results[0].title)
                if matched_article:
                    # Provide top tactical briefing info to Gemini
                    context = matched_article.full_tactical_brief[:2500]

            # 2. Generate response with Ministry of Truth persona
            answer = await self.persona.answer_tactical_query(
                query=question,
                context=context,
            )

            embed = discord.Embed(
                title="🦅 MINISTRY OF TRUTH TACTICAL TERMINAL",
                description=answer,
                color=TERMINAL_BLUE,
            )
            embed.set_footer(text=f"Inquiry from Helldiver {interaction.user.display_name} · Super Earth High Command")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            log.exception("Error executing /ask_ministry: %s", e)
            await interaction.followup.send(
                "⚠️ **Terminal Glitch:** Communication with the Ministry of Truth server was briefly interrupted by dissident interference. Try again shortly.",
                ephemeral=True,
            )

    @ask_ministry.error
    async def on_ask_ministry_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Handle 15-second cooldown error gracefully."""
        if isinstance(error, app_commands.CommandOnCooldown):
            seconds = int(error.retry_after)
            await interaction.response.send_message(
                f"⏱️ **Communications Protocol:** Please wait **{seconds} seconds** before pinging the Ministry of Truth again. Super Earth bandwidth is precious.",
                ephemeral=True,
            )
        else:
            log.error("Unhandled error in /ask_ministry: %s", error)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(WikiCog(bot))
