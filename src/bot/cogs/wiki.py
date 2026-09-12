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


# ─── Tactical Embed & Markdown Splitting Helpers ─────────────────────────────

def _get_unclosed_markdown_tags(text: str) -> tuple[str, str]:
    """Calculate closing suffix and opening prefix to balance unclosed markdown delimiters.

    Returns (closing_suffix, opening_prefix).
    """
    code_block_open = (text.count("```") % 2 != 0)

    sans_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    sans_code = re.sub(r"`[^`]*`", "", sans_code)
    inline_code_open = (sans_code.count("`") % 2 != 0)
    sans_code = re.sub(r"`", "", sans_code)

    bold_open = (sans_code.count("**") % 2 != 0)
    bold_under_open = (sans_code.count("__") % 2 != 0)
    strike_open = (sans_code.count("~~") % 2 != 0)

    # Italic single * (ignore list bullets at start of line and **)
    sans_bold = re.sub(r"\*\*", "", sans_code)
    sans_bullets = re.sub(r"(?m)^\s*[\*\-]\s+", "", sans_bold)
    italic_star_open = (sans_bullets.count("*") % 2 != 0)

    # Italic single _ (ignore __)
    sans_bold_u = re.sub(r"__", "", sans_code)
    italic_under_open = (sans_bold_u.count("_") % 2 != 0)

    closing_tags: list[str] = []
    opening_tags: list[str] = []

    if code_block_open:
        closing_tags.append("\n```")
        opening_tags.append("```\n")
    if bold_open:
        closing_tags.append("**")
        opening_tags.append("**")
    if bold_under_open:
        closing_tags.append("__")
        opening_tags.append("__")
    if strike_open:
        closing_tags.append("~~")
        opening_tags.append("~~")
    if italic_star_open:
        closing_tags.append("*")
        opening_tags.append("*")
    if italic_under_open:
        closing_tags.append("_")
        opening_tags.append("_")
    if inline_code_open:
        closing_tags.append("`")
        opening_tags.append("`")

    suffix = "".join(reversed(closing_tags))
    prefix = "".join(opening_tags)
    return suffix, prefix


def safe_markdown_split(text: str, max_len: int = 1000) -> list[str]:
    """Split markdown text into chunks of at most max_len without breaking markdown formatting.

    If delimiters (**, *, __, _, ~~, ```, `) are sliced across boundary cuts,
    they are automatically closed at the end of the current chunk and reopened
    at the start of the next chunk.
    """
    clean_text = text.strip()
    if not clean_text:
        return []
    if len(clean_text) <= max_len:
        suffix, _ = _get_unclosed_markdown_tags(clean_text)
        return [clean_text + suffix]

    chunks: list[str] = []
    remaining = clean_text
    active_prefix = ""

    while remaining:
        headroom = 24
        current_max = max(50, max_len - len(active_prefix) - headroom)

        if len(remaining) <= current_max:
            final_chunk = active_prefix + remaining
            suffix, _ = _get_unclosed_markdown_tags(final_chunk)
            chunks.append(final_chunk + suffix)
            break

        candidate = remaining[:current_max]

        # Natural split points: paragraph -> newline -> sentence -> word
        p_idx = candidate.rfind("\n\n")
        if p_idx >= 100:
            split_idx = p_idx + 2
        else:
            nl_idx = candidate.rfind("\n")
            if nl_idx >= 100:
                split_idx = nl_idx + 1
            else:
                sent_match = None
                for m in re.finditer(r"[.!?]\s+", candidate):
                    sent_match = m
                if sent_match and sent_match.end() >= 100:
                    split_idx = sent_match.end()
                else:
                    sp_idx = candidate.rfind(" ")
                    if sp_idx >= 100:
                        split_idx = sp_idx + 1
                    else:
                        split_idx = current_max

        # Prevent splitting inside multi-char markdown delimiters
        for delim in ("```", "**", "__", "~~"):
            for offset in range(1, len(delim)):
                if split_idx >= offset and split_idx + (len(delim) - offset) <= len(remaining):
                    if remaining[split_idx - offset : split_idx + (len(delim) - offset)] == delim:
                        split_idx -= offset
                        break

        chunk_raw = remaining[:split_idx].rstrip()
        remaining = remaining[split_idx:].lstrip()

        full_chunk = active_prefix + chunk_raw
        suffix, next_prefix = _get_unclosed_markdown_tags(full_chunk)

        chunks.append(full_chunk + suffix)
        active_prefix = next_prefix

    return chunks


def parse_tactical_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Extract the main overview narrative and individual sections from a tactical response."""
    # Look for markdown header boundaries (#{1,3} Header)
    pattern = r"(?:^|\n)(?:---+\s*\n)?(#{1,3}\s+[^\n]+)\n"
    matches = list(re.finditer(pattern, text))

    # If no #{1,3} headers, look for standalone bold section titles
    if not matches:
        bold_pattern = (
            r"(?:^|\n)(?:---+\s*\n)?"
            r"(\*\*(?:[A-Z0-9\s—–:-]{3,60}|(?:Authorized|Official|Ballistic|Tactical|High Command)[^*]+)\*\*)\s*:\s*\n"
        )
        matches = list(re.finditer(bold_pattern, text))

    if not matches:
        return text.strip(), []

    # Overview is everything before the first section
    overview = text[: matches[0].start()].strip()
    overview = re.sub(r"\n---+\s*$", "", overview).strip()

    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        raw_header = m.group(1).lstrip("#").strip()
        header = raw_header.strip("* :")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        body = re.sub(r"\n---+\s*$", "", body).strip()
        if body:
            sections.append((header, body))

    if not overview and sections:
        first_h, first_b = sections.pop(0)
        overview = f"**{first_h}**\n\n{first_b}"

    return overview, sections


def build_tactical_embeds(
    answer: str,
    matched_article: WikiArticle | None = None,
    user_display_name: str = "",
) -> list[discord.Embed]:
    """Build one or more Discord embeds for a tactical /ask response adhering to character limits."""
    overview, sections = parse_tactical_sections(answer)

    embeds: list[discord.Embed] = []
    primary_embed = discord.Embed(
        title="🦅 MINISTRY OF TRUTH TACTICAL TERMINAL",
        color=TERMINAL_BLUE,
    )

    if matched_article:
        primary_embed.set_footer(
            text=f"Source: {matched_article.title} ({matched_article.url}) · Super Earth High Command"
        )
    else:
        name_str = f"Helldiver {user_display_name}" if user_display_name else "Helldiver"
        primary_embed.set_footer(
            text=f"Inquiry from {name_str} · Super Earth High Command"
        )

    # Put overview narrative directly in embed.description (Discord limit: 4,096 chars)
    overview_overflow: list[str] = []
    if len(overview) <= 4000:
        primary_embed.description = overview
    else:
        ov_chunks = safe_markdown_split(overview, max_len=3950)
        primary_embed.description = ov_chunks[0]
        overview_overflow = ov_chunks[1:]

    current_embed = primary_embed
    embeds.append(current_embed)

    def add_field_safely(name: str, value: str) -> None:
        nonlocal current_embed
        # Discord limit: max 25 fields per embed, max 6,000 chars total embed
        current_len = (
            len(current_embed.title or "")
            + len(current_embed.description or "")
            + sum(len(f.name) + len(f.value) for f in current_embed.fields)
            + len(current_embed.footer.text or "")
        )
        if len(current_embed.fields) >= 20 or current_len + len(name) + len(value) > 5500:
            current_embed = discord.Embed(
                title="🦅 MINISTRY OF TRUTH TACTICAL TERMINAL (CONT.)",
                color=TERMINAL_BLUE,
            )
            embeds.append(current_embed)

        current_embed.add_field(name=name[:256], value=value[:1024], inline=False)

    # Add any overflow from overview as fields
    for idx, ov_chunk in enumerate(overview_overflow, start=1):
        field_chunks = safe_markdown_split(ov_chunk, max_len=1000)
        for f_idx, f_val in enumerate(field_chunks):
            suffix = f" (Part {idx}.{f_idx + 1})" if len(field_chunks) > 1 else f" (Part {idx})"
            add_field_safely(f"Overview Intelligence{suffix}", f_val)

    # Process tactical sections into fields
    for header, body in sections:
        base_header = header[:230].strip()
        chunks = safe_markdown_split(body, max_len=1000)
        if len(chunks) == 1:
            add_field_safely(base_header, chunks[0])
        else:
            for part_idx, chunk in enumerate(chunks, start=1):
                part_title = f"{base_header} (Part {part_idx})"
                add_field_safely(part_title, chunk)

    # Add verified source citation field if available
    if matched_article:
        add_field_safely(
            "📚 Verified Intelligence Source",
            f"[{matched_article.title}]({matched_article.url})",
        )

    return embeds


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
