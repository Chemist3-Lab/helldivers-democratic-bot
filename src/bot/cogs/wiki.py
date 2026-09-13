"""Ministry of Truth Tactical Terminal / Wiki RAG Cog.

Provides slash commands:
- `/stratagem <name>`: Look up a Super Earth Stratagem — call-in code, cooldown, uses, and payload stats.
- `/stats <query>`: Retrieve exhaustive tactical data on any enemy, weapon, armor, booster, or planet.
- `/ask <question>`: Ask the Ministry of Truth Tactical Terminal any Helldivers question.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands

from src.ai.persona import MinistryPersona
from src.bot.ui.markdown import (
    parse_tactical_sections,
    safe_markdown_split,
)
from src.bot.ui.wiki_embeds import (
    STOP_WORDS,
    STRATAGEM_INFOBOX_KEYS as UI_STRATAGEM_INFOBOX_KEYS,
    SUPER_EARTH_GOLD,
    TERMINAL_BLUE,
    _format_enemy_embed,
    _format_weapon_embed,
    _is_stratagem_by_categories,
    _is_stratagem_by_infobox,
    build_stats_embed,
    build_stratagem_embed,
    build_tactical_embeds,
    extract_wiki_entity,
    format_enemy_embed,
    format_weapon_embed,
    is_stratagem_by_categories,
    is_stratagem_by_infobox,
)
from src.services.wiki_client import WikiArticle, WikiClient

if TYPE_CHECKING:
    from src.bot.bot import HelldiversBot

log = logging.getLogger(__name__)

# Re-export infobox keys including druid keys: "base cooldown", "permit type"
STRATAGEM_INFOBOX_KEYS = UI_STRATAGEM_INFOBOX_KEYS

# Stratagem embed fields produced by build_stratagem_embed in src.bot.ui.wiki_embeds:
# - '🕹️ Stratagem Code / Input'
# - '⚙️ Tactical Specs'
# - '📋 Classification'
# - '💰 Procurement'
# - Footer: "Data source: helldivers.wiki.gg • Verified by High Command"

# Re-export public symbols for backwards compatibility
__all__ = [
    "STOP_WORDS",
    "STRATAGEM_INFOBOX_KEYS",
    "SUPER_EARTH_GOLD",
    "TERMINAL_BLUE",
    "WikiCog",
    "_format_enemy_embed",
    "_format_weapon_embed",
    "_is_stratagem_by_categories",
    "_is_stratagem_by_infobox",
    "build_stats_embed",
    "build_stratagem_embed",
    "build_tactical_embeds",
    "extract_wiki_entity",
    "format_enemy_embed",
    "format_weapon_embed",
    "is_stratagem_by_categories",
    "is_stratagem_by_infobox",
    "parse_tactical_sections",
    "safe_markdown_split",
    "setup",
]


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

    # ── /stratagem — strict stratagem-only lookup ────────────────────────────

    @app_commands.command(
        name="stratagem",
        description="Look up a Super Earth Stratagem — call-in code, cooldown, uses, and payload stats.",
    )
    @app_commands.describe(name="The exact Stratagem name (e.g., 'Orbital Railcannon Strike', '500kg Bomb', 'Shield Generator Relay')")
    async def stratagem(self, interaction: discord.Interaction, name: str) -> None:
        """Search helldivers.wiki.gg for a Stratagem and display call-in data."""
        await interaction.response.defer(thinking=True)

        try:
            results = await self.wiki.search(name, limit=3)
            if not results:
                cleaned = extract_wiki_entity(name)
                if cleaned and cleaned != name.lower():
                    results = await self.wiki.search(cleaned, limit=3)

            if not results:
                await interaction.followup.send(
                    f"🔍 **No Official Dossier Found:** No matching Stratagem `{name}` was located in the Super Earth Archives.",
                    ephemeral=True,
                )
                return

            best_match = results[0]
            article = await self.wiki.get_article(
                best_match.title,
                section=best_match.section,
            )

            if not article:
                await interaction.followup.send(
                    f"⚠️ **Data Corrupted:** Unable to parse dossier for **{best_match.title}**. Visit: {best_match.url}",
                    ephemeral=True,
                )
                return

            # Validate this is actually a Stratagem
            is_strat = False
            try:
                categories = await self.wiki.get_page_categories(best_match.title)
                is_strat = is_stratagem_by_categories(categories)
            except Exception:
                log.warning("Category check failed for '%s', falling back to infobox heuristic", best_match.title)

            if not is_strat:
                is_strat = is_stratagem_by_infobox(article.infobox)

            if not is_strat:
                await interaction.followup.send(
                    f"⚠️ **Requisition Error:** '{name}' is not an authorized Super Earth Stratagem. "
                    "Use `/stats` for enemy, ballistic, and planetary telemetry.",
                    ephemeral=True,
                )
                return

            embed = build_stratagem_embed(article)
            await interaction.followup.send(embed=embed)

        except Exception:
            log.exception("Error fulfilling /stratagem query for '%s'", name)
            await interaction.followup.send(
                "❌ Error contacting the Super Earth archives. Please retry shortly.",
                ephemeral=True,
            )

    # ── /stats — exhaustive tactical breakdown ───────────────────────────────

    @app_commands.command(
        name="stats",
        description="Retrieve exhaustive tactical data on any enemy, weapon, armor, booster, or planet.",
    )
    @app_commands.describe(query="Name of enemy, weapon, armor, booster, or planet (e.g., 'Bile Titan', 'Breaker', 'Automaton Gunship')")
    async def stats(self, interaction: discord.Interaction, query: str) -> None:
        """Search helldivers.wiki.gg and display an exhaustive tactical data breakdown."""
        await interaction.response.defer(thinking=True)

        try:
            results = await self.wiki.search(query, limit=3)
            if not results:
                cleaned = extract_wiki_entity(query)
                if cleaned and cleaned != query.lower():
                    results = await self.wiki.search(cleaned, limit=3)

            if not results:
                await interaction.followup.send(
                    f"🔍 **No Official Dossier Found:** No matching intelligence on `{query}` was located in the Super Earth Archives.",
                    ephemeral=True,
                )
                return

            best_match = results[0]
            article = await self.wiki.get_article(
                best_match.title,
                section=best_match.section,
            )

            if not article:
                await interaction.followup.send(
                    f"⚠️ **Data Corrupted:** Unable to parse dossier for **{best_match.title}**. Visit: {best_match.url}",
                    ephemeral=True,
                )
                return

            embed = build_stats_embed(article)
            await interaction.followup.send(embed=embed)

        except Exception:
            log.exception("Error fulfilling /stats query for '%s'", query)
            await interaction.followup.send(
                "❌ Error contacting the Super Earth archives. Please retry shortly.",
                ephemeral=True,
            )

    # ── /ask — Ministry of Truth AI terminal ─────────────────────────────────

    @app_commands.command(
        name="ask",
        description="Ask the Ministry of Truth Tactical Terminal any Helldivers question.",
    )
    @app_commands.describe(question="Your tactical inquiry for the Ministry of Truth")
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: (i.guild_id, i.user.id))
    async def ask(self, interaction: discord.Interaction, question: str) -> None:
        """Invoke the Ministry of Truth persona with optional wiki context."""
        await interaction.response.defer(thinking=True)

        try:
            # 1. Look up any relevant wiki context to ground the response in real stats
            context = ""
            matched_article: WikiArticle | None = None
            try:
                entity = extract_wiki_entity(question)
                search_results = await self.wiki.search(entity, limit=3)
                if not search_results and entity != question.lower():
                    search_results = await self.wiki.search(question, limit=1)

                if search_results:
                    top_match = search_results[0]
                    matched_article = await self.wiki.get_article(
                        top_match.title,
                        section=top_match.section,
                    )
                    if matched_article:
                        context = matched_article.get_tactical_brief(
                            target_section=top_match.section,
                            max_chars=30000,
                        )
                        log.info(
                            "Injected wiki context for '%s' (matched: %s, section: %s, %d chars)",
                            entity,
                            matched_article.title,
                            top_match.section,
                            len(context),
                        )
            except Exception as wiki_err:
                log.warning("Wiki lookup failed during /ask (continuing without wiki context): %s", wiki_err)

            # 2. Generate response with Ministry of Truth persona
            answer = await self.persona.answer_tactical_query(
                query=question,
                context=context,
            )

            # 3. Build structured Discord embeds adhering to description (4,096) and field (1,000) limits
            embeds = build_tactical_embeds(
                answer=answer,
                matched_article=matched_article,
                user_display_name=interaction.user.display_name,
            )
            for emb in embeds:
                await interaction.followup.send(embed=emb)

        except Exception as e:
            log.exception("Error during /ask execution: %s", e)
            await interaction.followup.send(
                "⚠️ **Terminal Glitch:** Communication with the Ministry of Truth server was briefly interrupted by dissident interference. Try again shortly.",
                ephemeral=True,
            )

    @ask.error
    async def on_ask_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Handle 15-second cooldown error gracefully."""
        if isinstance(error, app_commands.CommandOnCooldown):
            seconds = int(error.retry_after)
            await interaction.response.send_message(
                f"⏱️ **Communications Protocol:** Please wait **{seconds} seconds** before pinging the Ministry of Truth again. Super Earth bandwidth is precious.",
                ephemeral=True,
            )
        else:
            log.error("Unhandled error in /ask: %s", error)
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ An unexpected error occurred.", ephemeral=True)


async def setup(bot: HelldiversBot) -> None:
    await bot.add_cog(WikiCog(bot))
