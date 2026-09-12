"""Ministry of Truth Tactical Terminal / Wiki RAG Cog.

Provides slash commands:
- `/stratagem <name>`: Look up a Super Earth Stratagem — call-in code, cooldown, uses, and payload stats.
- `/stats <query>`: Retrieve exhaustive tactical data on any enemy, weapon, armor, booster, or planet.
- `/ask <question>`: Ask the Ministry of Truth Tactical Terminal any Helldivers question.
"""

from __future__ import annotations

import logging
import re
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

# Stop words and question fluff to strip when extracting core wiki search terms
STOP_WORDS = {
    "how", "much", "many", "hp", "health", "armor", "damage",
    "does", "do", "did", "a", "an", "the", "have", "has", "had",
    "what", "whats", "what's", "is", "are", "was", "were",
    "where", "when", "why", "who", "which",
    "to", "of", "in", "on", "for", "with", "at", "by", "from",
    "kill", "destroy", "defeat", "beat", "counter",
    "tell", "me", "about", "can", "i", "you", "we", "please",
    "explain", "give", "stats", "info", "information",
    "there", "exist", "game", "currently", "now", "total", "all",
}

# Infobox keys that indicate a page is a Stratagem
STRATAGEM_INFOBOX_KEYS = {
    "call-in time", "cooldown", "uses", "input", "stratagem code",
    "call-in", "activation", "directional input",
    "base cooldown", "permit type",  # druid-infobox keys from helldivers.wiki.gg
}


def extract_wiki_entity(text: str) -> str:
    """Extract core subject entity from natural language question for MediaWiki search.

    Strips question fluff and stop words:
    'how much hp does a bile titan have?' -> 'bile titan'
    'what is the quasar cannon?' -> 'quasar cannon'
    'tell me about the charger' -> 'charger'
    """
    cleaned = re.sub(r"[^\w\s-]", " ", text.lower())
    tokens = [w for w in cleaned.split() if w not in STOP_WORDS]
    result = " ".join(tokens).strip()
    return result or text.strip(" ?.!/\\\"")


def _is_stratagem_by_categories(categories: list[str]) -> bool:
    """Check if any wiki category indicates this page is a Stratagem."""
    for cat in categories:
        if "stratagem" in cat.lower():
            return True
    return False


def _is_stratagem_by_infobox(infobox: dict[str, str]) -> bool:
    """Fallback check: does the infobox contain stratagem-specific keys?"""
    lower_keys = {k.lower() for k in infobox}
    return bool(lower_keys & STRATAGEM_INFOBOX_KEYS)


def _format_weapon_embed(embed: discord.Embed, article: WikiArticle) -> bool:
    """Format structured embed fields for a weapon. Returns True if handled as a weapon."""
    ib = article.infobox
    is_weapon = bool(
        ib.get("Weapon Category")
        or ib.get("Weapon Type")
        or ib.get("Standard Damage")
        or ib.get("DPS")
        or ("Detailed Weapon Statistics" in article.sections and any(k in ib for k in ("Damage", "Fire Rate", "Capacity", "Recoil")))
    )
    if not is_weapon:
        return False

    # 1. Type / Traits
    cat = ib.get("Weapon Category", "").replace("Weapons", "").strip()
    wtype = ib.get("Weapon Type", "").rstrip("s").strip()
    type_str = f"{cat} {wtype}".strip() or "Support Weapon"
    traits = ib.get("Traits", "")
    clean_traits = re.sub(r"\s*[•·]\s*", ", ", traits).strip()
    if clean_traits:
        type_traits_val = f"{type_str} | {clean_traits}" if type_str else clean_traits
    else:
        type_traits_val = type_str or "Standard Armament"
    embed.add_field(name="📋 Type / Traits", value=type_traits_val, inline=False)

    # 2. Ballistics
    dmg = ib.get("Standard Damage", "")
    fr = ib.get("Fire Rate", "")
    dps = ib.get("DPS", "")
    ap = ib.get("Armor Penetration", "")
    ballistics_parts: list[str] = []
    if dmg:
        ballistics_parts.append(f"Damage: {dmg}")
    if fr:
        ballistics_parts.append(f"Fire Rate: {fr}")
    if dps:
        ballistics_parts.append(f"DPS: {dps}")
    if ap and ap.lower() not in type_traits_val.lower():
        ballistics_parts.append(f"Penetration: {ap}")
    if ballistics_parts:
        embed.add_field(name="💥 Ballistics", value=" | ".join(ballistics_parts), inline=False)

    # 3. Ammunition
    cap = ib.get("Capacity", "")
    mags = ib.get("Spare Mags", "")
    ammo_parts: list[str] = []
    if cap:
        ammo_parts.append(f"Capacity: {cap}")
    if mags:
        ammo_parts.append(f"Spare Mags: {mags}")
    if ammo_parts:
        embed.add_field(name="📦 Ammunition", value=" | ".join(ammo_parts), inline=True)

    # 4. Handling
    recoil = ib.get("Recoil", "")
    ergo = ib.get("Ergonomics", "")
    reload_t = ib.get("Reload Time", "") or ib.get("Tactical Reload Time", "")
    handling_parts: list[str] = []
    if recoil:
        handling_parts.append(f"Recoil: {recoil}")
    if ergo:
        handling_parts.append(f"Ergonomics: {ergo}")
    if reload_t:
        handling_parts.append(f"Reload: {reload_t}")
    if handling_parts:
        embed.add_field(name="🎯 Handling", value=" | ".join(handling_parts), inline=True)

    # 5. Procurement
    source = ib.get("Source", "")
    cost = ib.get("Unlock Cost", "")
    if source or cost:
        proc: list[str] = []
        if source:
            proc.append(f"Source: {source}")
        if cost:
            proc.append(f"Cost: {cost}")
        embed.add_field(name="💰 Procurement", value=" | ".join(proc), inline=False)

    return True


def _format_enemy_embed(embed: discord.Embed, article: WikiArticle) -> bool:
    """Format structured embed fields for an enemy. Returns True if handled as an enemy."""
    ib = article.infobox
    anatomy_sec = article.sections.get("Anatomy", "")
    is_enemy = bool(
        anatomy_sec
        or ("Faction" in ib and any(f in ib.get("Faction", "") for f in ("Terminid", "Automaton", "Illuminate")))
        or ("Stagger Threshold" in ib and "Health" in ib)
    )
    if not is_enemy:
        return False

    # Extract rows from Anatomy section if present
    parts: list[dict[str, str]] = []
    if anatomy_sec:
        for line in anatomy_sec.splitlines():
            line = line.strip()
            if not line.startswith("|") or "Part Name" not in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            part_data: dict[str, str] = {}
            for cell in cells:
                if ":" in cell:
                    k, v = cell.split(":", 1)
                    part_data[k.strip()] = v.strip()
            if "Part Name" in part_data:
                parts.append(part_data)

    # 1. Main Health
    main_part = next((p for p in parts if p.get("Part Name", "").lower() == "main"), None)
    body_hp = (main_part.get("Health") if main_part else None) or ib.get("Health", "Unknown")
    armor_class = (main_part.get("AV") if main_part else None) or ib.get("Size Class", "Standard")
    embed.add_field(
        name="❤️ Main Health",
        value=f"**Body HP:** {body_hp} | **Armor Class:** {armor_class}",
        inline=False,
    )

    # 2. Weakpoints: parts with Fatal?: Yes (other than Main) or key anatomies
    weakpoints: list[str] = []
    seen_weak: set[str] = set()
    for p in parts:
        pname = p.get("Part Name", "")
        if pname.lower() == "main":
            continue
        fatal = p.get("Fatal?", "")
        av = p.get("AV", "")
        hp = p.get("Health", "")
        is_weak = "yes" in fatal.lower() or pname.lower() in ("head", "mouth", "eye", "vent", "heatsink", "butt", "butt / abdomen", "underside")
        if is_weak and pname.lower() not in seen_weak:
            seen_weak.add(pname.lower())
            fatal_str = f" | Fatal: {fatal}" if fatal else ""
            weakpoints.append(f"• **{pname}:** {hp} HP (Armor: {av}{fatal_str})")

    if weakpoints:
        embed.add_field(name="🎯 Weakpoints", value="\n".join(weakpoints[:5]), inline=False)
    elif "Damage" in ib:
        embed.add_field(name="⚔️ Combat Profile", value=f"**Threat:** {ib.get('Damage', 'Standard')}", inline=False)

    # 3. Limbs / Secondary
    secondary: list[str] = []
    for p in parts:
        pname = p.get("Part Name", "")
        if pname.lower() == "main" or pname.lower() in seen_weak:
            continue
        hp = p.get("Health", "")
        av = p.get("AV", "")
        if hp and av:
            secondary.append(f"• **{pname}:** {hp} HP (AV: {av})")

    if secondary:
        embed.add_field(name="🛡️ Limbs / Secondary", value="\n".join(secondary[:6]), inline=False)

    # Intel summary
    info: list[str] = []
    if "Faction" in ib:
        info.append(f"**Faction:** {ib['Faction']}")
    if "Minimum Difficulty" in ib:
        info.append(f"**Min Difficulty:** {ib['Minimum Difficulty']}")
    if info:
        embed.add_field(name="📋 Intel", value=" | ".join(info), inline=False)

    return True


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

            # ── Validate this is actually a Stratagem ──
            is_stratagem = False
            try:
                categories = await self.wiki.get_page_categories(best_match.title)
                is_stratagem = _is_stratagem_by_categories(categories)
            except Exception:
                log.warning("Category check failed for '%s', falling back to infobox heuristic", best_match.title)

            if not is_stratagem:
                is_stratagem = _is_stratagem_by_infobox(article.infobox)

            if not is_stratagem:
                await interaction.followup.send(
                    f"⚠️ **Requisition Error:** '{name}' is not an authorized Super Earth Stratagem. "
                    "Use `/stats` for enemy, ballistic, and planetary telemetry.",
                    ephemeral=True,
                )
                return

            # ── Build structured stratagem embed ──
            strat_desc = (
                f'"{article.quote}"'
                if article.quote
                else (article.summary[:600] if article.summary else "Authorized stratagem deployment data.")
            )

            embed = discord.Embed(
                title=f"🎯 STRATAGEM REQUISITION: {article.title}",
                url=article.url,
                description=strat_desc,
                color=SUPER_EARTH_GOLD,
            )

            # Stratagem Code / Input (directional input arrows)
            strat_code = article.infobox.get("Stratagem Code", "")
            if strat_code:
                embed.add_field(name="🕹️ Stratagem Code / Input", value=f"**{strat_code}**", inline=False)

            # Tactical Specs group
            call_in = article.infobox.get("Call-in Time", "")
            uses = article.infobox.get("Uses", "")
            cooldown = article.infobox.get("Cooldown") or article.infobox.get("Base Cooldown", "")
            specs: list[str] = []
            if call_in:
                specs.append(f"• **Call-in Time:** {call_in}")
            if uses:
                specs.append(f"• **Uses:** {uses}")
            if cooldown:
                specs.append(f"• **Cooldown:** {cooldown}")
            if not specs:
                for sec_title, sec_content in article.sections.items():
                    if "stratagem statistics" in sec_title.lower() and sec_content.strip():
                        for line in sec_content.strip().splitlines()[:6]:
                            cleaned = line.strip("| ").strip()
                            if cleaned and "Tactical Data" not in cleaned:
                                specs.append(cleaned)
                        break
            if specs:
                embed.add_field(name="⚙️ Tactical Specs", value="\n".join(specs[:6]), inline=True)

            # Classification group (Permit Type, Traits)
            meta: list[str] = []
            for key in ("Permit Type", "Traits"):
                val = article.infobox.get(key)
                if val:
                    meta.append(f"• **{key}:** {val}")
            if meta:
                embed.add_field(name="📋 Classification", value="\n".join(meta), inline=True)

            # Procurement group (Unlock Level, Cost, Source)
            procurement: list[str] = []
            lvl = article.infobox.get("Unlock Level")
            if lvl:
                lvl_str = f"Level {lvl}" if lvl.isdigit() else lvl
                procurement.append(f"• **Unlock Level:** {lvl_str}")
            cost = article.infobox.get("Unlock Cost")
            if cost:
                cost_clean = re.sub(r"\b(\d{4,})\b", lambda m: f"{int(m.group(1)):,}", cost)
                procurement.append(f"• **Cost:** {cost_clean}")
            src_val = article.infobox.get("Source")
            if src_val:
                procurement.append(f"• **Source:** {src_val}")
            if procurement:
                embed.add_field(name="💰 Procurement", value="\n".join(procurement), inline=False)

            embed.set_author(
                name="Super Earth Stratagem Requisition Terminal",
                icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
            )
            embed.set_footer(
                text="Data source: helldivers.wiki.gg • Verified by High Command"
            )
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

            # Build clean embed
            embed = discord.Embed(
                title=f"📖 TACTICAL DOSSIER: {article.title}",
                url=article.url,
                description=article.summary[:800] if article.summary else "Official tactical report filed in archives.",
                color=SUPER_EARTH_GOLD,
            )

            # 1. Specialized Weapon formatting
            handled = _format_weapon_embed(embed, article)

            # 2. Specialized Enemy formatting
            if not handled:
                handled = _format_enemy_embed(embed, article)

            # 3. Fallback: general infobox & clean sections (for planets, boosters, general lore)
            if not handled:
                if article.infobox:
                    count = 0
                    for k, v in article.infobox.items():
                        if count >= 15:
                            break
                        embed.add_field(name=k, value=v[:200], inline=True)
                        count += 1

                _SKIP_SECTIONS = {"overview", "media", "gallery", "trivia", "change history", "references", "see also"}
                sec_count = 0
                for sec_title, sec_content in article.sections.items():
                    if sec_count >= 5:
                        break
                    if not sec_content.strip():
                        continue
                    if sec_title.lower() in _SKIP_SECTIONS:
                        continue
                    truncated = sec_content.strip()[:1024]
                    if len(sec_content.strip()) > 1024:
                        last_nl = truncated.rfind("\n")
                        truncated = (truncated[:last_nl] if last_nl > 200 else truncated[:1021]) + "…"
                    embed.add_field(name=f"📊 {sec_title}", value=truncated, inline=False)
                    sec_count += 1

            embed.set_author(
                name="Super Earth Ministry of Truth Archives",
                icon_url="https://images.wikia.com/helldivers/images/4/47/Super_Earth_Logo.png",
            )
            embed.set_footer(text="Data source: helldivers.wiki.gg • Verified by High Command")
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

            embed = discord.Embed(
                title="🦅 MINISTRY OF TRUTH TACTICAL TERMINAL",
                color=TERMINAL_BLUE,
            )
            if matched_article:
                embed.set_footer(
                    text=f"Source: {matched_article.title} ({matched_article.url}) · Super Earth High Command"
                )
            else:
                embed.set_footer(
                    text=f"Inquiry from Helldiver {interaction.user.display_name} · Super Earth High Command"
                )

            # Discord embeds limit description to 4096 characters; chunk into fields if answer is long
            if len(answer) <= 4000:
                embed.description = answer
                if matched_article:
                    embed.add_field(
                        name="📚 Verified Intelligence Source",
                        value=f"[{matched_article.title}]({matched_article.url})",
                        inline=False,
                    )
                await interaction.followup.send(embed=embed)
            else:
                split_idx = answer[:3950].rfind("\n")
                if split_idx <= 0:
                    split_idx = 3950
                embed.description = answer[:split_idx].strip()
                remainder = answer[split_idx:].strip()

                chunk_num = 1
                while remainder and len(embed.fields) < 20:
                    if len(remainder) <= 1000:
                        field_val = remainder
                        remainder = ""
                    else:
                        c_idx = remainder[:950].rfind("\n")
                        if c_idx <= 0:
                            c_idx = 950
                        field_val = remainder[:c_idx].strip()
                        remainder = remainder[c_idx:].strip()
                    embed.add_field(
                        name=f"Tactical Telemetry (Cont. {chunk_num})",
                        value=field_val,
                        inline=False,
                    )
                    chunk_num += 1

                if matched_article:
                    embed.add_field(
                        name="📚 Verified Intelligence Source",
                        value=f"[{matched_article.title}]({matched_article.url})",
                        inline=False,
                    )
                await interaction.followup.send(embed=embed)
                if remainder:
                    cont_embed = discord.Embed(
                        description=remainder[:4000],
                        color=TERMINAL_BLUE,
                    )
                    await interaction.followup.send(embed=cont_embed)

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
