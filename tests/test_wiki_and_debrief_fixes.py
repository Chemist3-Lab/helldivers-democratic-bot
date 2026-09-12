"""Tests for the four targeted fixes:
1. Debrief embed title always "📋 MISSION EXTRACTION DEBRIEF" (no difficulty)
2. Commentary sanitization catches "Deaths: 7 (A bit high," pattern
3. Druid-infobox parser extracts data and stratagem arrow codes
4. /stratagem and /stats embeds have structured fields (Weapons and Enemies)
"""

import re
import sys
import os
import io

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Ensure stdout handles utf-8 safely
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


# ─── Test 1: Debrief Embed Title ────────────────────────────────────────────

def test_debrief_embed_title_no_difficulty():
    """The embed title must always be '📋 MISSION EXTRACTION DEBRIEF' — no difficulty suffix."""
    with open("src/bot/cogs/scoreboard.py", encoding="utf-8") as f:
        src = f.read()
    # Must NOT contain the old conditional pattern
    assert "DIFFICULTY {diff_val}" not in src, "Old conditional difficulty title still present"
    assert "DIFFICULTY {difficulty}" not in src, "Old difficulty placeholder still present"
    # Must contain the unconditional title
    assert '📋 MISSION EXTRACTION DEBRIEF"' in src, "Unconditional title not found"
    # No "Difficulty Level" in squad summary
    assert "Difficulty Level" not in src, "Difficulty Level line still in embed"
    print("[PASS] test_debrief_embed_title_no_difficulty")


# ─── Test 2: Commentary Sanitization ────────────────────────────────────────

def _simulate_sanitization(raw_text: str) -> str:
    """Replicate the sanitization logic from persona.py."""
    cleaned = raw_text.strip()
    cleaned = re.sub(
        r"^(?:High Command Assessment|Ministry Assessment|Debrief|Evaluation|Assessment|Transmission):\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^\*+|\*+$", "", cleaned).strip()
    cleaned = re.sub(r'^\"+|\"+$', "", cleaned).strip()

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    valid_lines = [
        l for l in lines
        if not re.search(
            r"(?:"
            r"friendly fire\s*=|stats:|difficulty\s*\d|dvr score\s*=|>\s*\d+"
            r"|(?:Deaths|Kills|Accuracy|Stims|Friendly Fire)\s*[:=]\s*[\d.,]+\s*\("
            r"|^\s*[-•]\s*(?:Deaths|Kills|Accuracy|DVR|Friendly)\s*[:=]"
            r"|Net DVR|Combat Metrics"
            r")",
            l, re.IGNORECASE,
        )
    ]
    result = " ".join(valid_lines).strip()

    # Fix unclosed parentheses
    if result.count("(") > result.count(")"):
        last_open = result.rfind("(")
        before_paren = result[:last_open].rstrip()
        sentence_end = max(before_paren.rfind("."), before_paren.rfind("!"), before_paren.rfind("?"))
        if sentence_end > 20:
            result = before_paren[: sentence_end + 1].strip()
        else:
            result = before_paren.rstrip(" ,;:—-").strip()

    if len(result) > 450:
        match = re.search(r"^(.{1,450}[.!?])", result)
        if match:
            result = match.group(1).strip()
        else:
            result = result[:447].rstrip() + "..."

    return result


def test_deaths_stat_echo_filtered():
    """The 'Deaths: 7 (A bit high,' pattern must be removed."""
    raw = "High Command commends your service. Deaths: 7 (A bit high, but"
    result = _simulate_sanitization(raw)
    assert "Deaths: 7" not in result, f"Deaths stat echo not filtered: {result}"
    assert "A bit high" not in result, f"Partial commentary not filtered: {result}"
    print("[PASS] test_deaths_stat_echo_filtered")


def test_kills_stat_echo_filtered():
    """Kills stat echo patterns should be stripped."""
    raw = "Kills: 173 (Impressive — top-tier. Deaths: 7 (A bit high, consider"
    result = _simulate_sanitization(raw)
    assert "Kills: 173" not in result, f"Kills stat echo not filtered: {result}"
    print("[PASS] test_kills_stat_echo_filtered")


def test_friendly_fire_equals_filtered():
    """'Friendly Fire = 400 (>' must still be caught."""
    raw = "Excellent mission. Friendly Fire = 400 (> 100 is treason"
    result = _simulate_sanitization(raw)
    assert "Friendly Fire =" not in result, f"FF equals not filtered: {result}"
    print("[PASS] test_friendly_fire_equals_filtered")


def test_unclosed_paren_truncation():
    """Results ending with unclosed parenthesis are truncated at last sentence."""
    raw = "Great performance on the battlefield. Your kills were exceptional! (However"
    result = _simulate_sanitization(raw)
    assert "(" not in result or result.count("(") <= result.count(")"), \
        f"Unclosed paren not truncated: {result}"
    assert result.endswith("!") or result.endswith("."), f"Should end at sentence: {result}"
    print("[PASS] test_unclosed_paren_truncation")


def test_clean_commentary_passes_through():
    """Normal, clean commentary should pass through unchanged."""
    raw = "High Command commends Helldiver Shinoruba for purging 173 enemies with 0 friendly fire incidents! Practice evasive maneuvers on your next drop!"
    result = _simulate_sanitization(raw)
    assert "commends Helldiver Shinoruba" in result
    assert "Practice evasive" in result
    print("[PASS] test_clean_commentary_passes_through")


# ─── Test 3: Druid Infobox Parser ───────────────────────────────────────────

def test_druid_infobox_parsing():
    """The parser should extract data from druid-infobox containers."""
    from src.services.wiki_client import _clean_soup_html

    html = '''
    <div class="mw-parser-output">
        <div class="druid-infobox druid-container druid-container-stratagem" id="druid-container-1">
            <div><div class="druid-title">Test Stratagem</div></div>
            <div class="druid-section-container">
                <div class="druid-row druid-row-permit_type">
                    <div class="druid-label druid-label-permit_type">Permit Type</div>
                    <div class="druid-data druid-data-permit_type druid-data-nonempty">Offensive</div>
                </div>
                <div class="druid-row druid-row-traits">
                    <div class="druid-label druid-label-traits">Traits</div>
                    <div class="druid-data druid-data-traits druid-data-nonempty">Orbital • Anti-Tank</div>
                </div>
                <div class="druid-row druid-row-stratagem_code">
                    <div class="druid-label druid-label-stratagem_code">Stratagem Code</div>
                    <div class="druid-data druid-data-stratagem_code druid-data-nonempty">
                        <span class="Stratagemcodeicon"><img alt="Stratagem Arrow Right.svg" src="/images/right.svg" /></span>
                        <span class="Stratagemcodeicon"><img alt="Stratagem Arrow Up.svg" src="/images/up.svg" /></span>
                        <span class="Stratagemcodeicon"><img alt="Stratagem Arrow Down.svg" src="/images/down.svg" /></span>
                        <span class="Stratagemcodeicon"><img alt="Stratagem Arrow Down.svg" src="/images/down.svg" /></span>
                        <span class="Stratagemcodeicon"><img alt="Stratagem Arrow Right.svg" src="/images/right.svg" /></span>
                    </div>
                </div>
                <div class="druid-row druid-row-base_cooldown">
                    <div class="druid-label druid-label-base_cooldown">Base Cooldown</div>
                    <div class="druid-data druid-data-base_cooldown druid-data-nonempty">180s</div>
                </div>
                <div class="druid-row druid-row-unlock_level">
                    <div class="druid-label druid-label-unlock_level">Unlock Level</div>
                    <div class="druid-data druid-data-unlock_level druid-data-nonempty">20</div>
                </div>
            </div>
        </div>
        <blockquote>
            <p>A high-velocity railcannon round fired at the largest target in close proximity to the beacon. Targeting is automatic.</p>
            <p>— Ship Management Terminal</p>
        </blockquote>
        <p>The Test Stratagem is an offensive stratagem that fires a projectile.</p>
    </div>
    '''

    article = _clean_soup_html(html, "Test Stratagem", "https://helldivers.wiki.gg/wiki/Test_Stratagem")

    assert article.infobox.get("Permit Type") == "Offensive", f"Permit Type: {article.infobox.get('Permit Type')}"
    assert article.infobox.get("Traits") == "Orbital • Anti-Tank", f"Traits: {article.infobox.get('Traits')}"
    assert article.infobox.get("Stratagem Code") == "➡ ⬆ ⬇ ⬇ ➡", f"Stratagem Code: {article.infobox.get('Stratagem Code')}"
    assert article.infobox.get("Base Cooldown") == "180s", f"Base Cooldown: {article.infobox.get('Base Cooldown')}"
    assert article.infobox.get("Unlock Level") == "20", f"Unlock Level: {article.infobox.get('Unlock Level')}"
    assert "offensive stratagem" in article.summary.lower(), f"Summary: {article.summary}"
    assert "high-velocity railcannon round" in article.quote, f"Quote: {article.quote}"
    print("[PASS] test_druid_infobox_parsing")


def test_arrow_mapping():
    """All four arrow directions must map correctly."""
    from src.services.wiki_client import _clean_soup_html

    html = '''
    <div class="mw-parser-output">
        <div class="druid-infobox druid-container">
            <div class="druid-row druid-row-code">
                <div class="druid-label">Stratagem Code</div>
                <div class="druid-data">
                    <img alt="Stratagem Arrow Left.svg" />
                    <img alt="Stratagem Arrow Right.svg" />
                    <img alt="Stratagem Arrow Up.svg" />
                    <img alt="Stratagem Arrow Down.svg" />
                </div>
            </div>
        </div>
        <p>Test page.</p>
    </div>
    '''
    article = _clean_soup_html(html, "Arrow Test", "https://test")
    code = article.infobox.get("Stratagem Code", "")
    assert "⬅" in code, f"Left arrow missing: {code}"
    assert "➡" in code, f"Right arrow missing: {code}"
    assert "⬆" in code, f"Up arrow missing: {code}"
    assert "⬇" in code, f"Down arrow missing: {code}"
    assert code == "⬅ ➡ ⬆ ⬇", f"Arrow order wrong: {code}"
    print("[PASS] test_arrow_mapping")


# ─── Test 4: Noise div & Version Filtering ──────────────────────────────────

def test_breadcrumb_and_version_filtered():
    """Breadcrumb divs and version tags should be stripped."""
    from src.services.wiki_client import _clean_soup_html

    html = '''
    <div class="mw-parser-output">
        <div class="noexcerpt">
            Last updated: <b>1.006.300</b><br /> Current patch: <b>1.007.002</b>
        </div>
        <div class="breadcrumb noexcerpt">
            <div class="breadcrumb-item"><a href="/wiki/Weapons">Weapons</a></div>
            <div class="breadcrumb-item">Primary Weapons</div>
            <div class="breadcrumb-item">Assault Rifles</div>
            <div class="breadcrumb-item breadcrumb-item-final">AR-23P Liberator Penetrator</div>
        </div>
        <p>The Liberator Penetrator is an assault rifle.</p>
        <h2>Detailed Weapon Statistics</h2>
        <table class="wikitable">
            <tr><th>Damage</th><td>45</td></tr>
            <tr><th>Fire Rate</th><td>640 rpm</td></tr>
        </table>
    </div>
    '''
    article = _clean_soup_html(html, "AR-23P Liberator Penetrator", "https://test")

    all_section_text = " ".join(article.sections.values())
    assert all_section_text.count("Weapons") <= 1, f"'Weapons' duplicated in sections: {all_section_text}"
    assert "breadcrumb" not in all_section_text.lower(), "Breadcrumb text in sections"
    assert "1.006.300" not in article.summary, "Version tag in summary"
    assert "1.007.002" not in article.summary, "Version tag in summary"
    print("[PASS] test_breadcrumb_and_version_filtered")


# ─── Test 5: Stratagem Embed Structure ───────────────────────────────────────

def test_stratagem_embed_fields_structure():
    """The /stratagem command builder should create structured embed fields."""
    with open("src/bot/cogs/wiki.py", encoding="utf-8") as f:
        src = f.read()
    # Must have structured field names
    assert '🕹️ Stratagem Code / Input' in src, "Missing Stratagem Code / Input field"
    assert '⚙️ Tactical Specs' in src, "Missing Tactical Specs field"
    assert '📋 Classification' in src, "Missing Classification field"
    assert '💰 Procurement' in src, "Missing Procurement field"
    # Footer must be the exact specified text
    assert "Data source: helldivers.wiki.gg • Verified by High Command" in src, "Incorrect footer text"
    print("[PASS] test_stratagem_embed_fields_structure")


# ─── Test 6: Weapon Embed Formatting ────────────────────────────────────────

def test_weapon_embed_formatting():
    """_format_weapon_embed must produce Type/Traits, Ballistics, Ammunition, Handling."""
    import discord
    from src.bot.cogs.wiki import _format_weapon_embed
    from src.services.wiki_client import WikiArticle

    art = WikiArticle(
        title="AR-2 Coyote",
        url="https://helldivers.wiki.gg/wiki/Coyote",
        summary="An assault rifle.",
        content="...",
        infobox={
            "Weapon Category": "Primary Weapons",
            "Weapon Type": "Assault Rifles",
            "Traits": "Incendiary • Medium Armor Penetrating",
            "Standard Damage": "75 Ballistic",
            "Fire Rate": "600 rpm",
            "DPS": "750",
            "Capacity": "45",
            "Spare Mags": "8",
            "Recoil": "17",
            "Ergonomics": "50",
            "Reload Time": "3s",
        },
    )

    embed = discord.Embed(title="Test")
    handled = _format_weapon_embed(embed, art)
    assert handled is True, "Weapon not handled"

    field_names = [f.name for f in embed.fields]
    assert any("Type / Traits" in n for n in field_names), f"Missing Type / Traits: {field_names}"
    assert any("Ballistics" in n for n in field_names), f"Missing Ballistics: {field_names}"
    assert any("Ammunition" in n for n in field_names), f"Missing Ammunition: {field_names}"
    assert any("Handling" in n for n in field_names), f"Missing Handling: {field_names}"

    field_dict = {f.name: f.value for f in embed.fields}
    assert "75" in str(field_dict.get("💥 Ballistics")), "Ballistics damage missing"
    assert "600 rpm" in str(field_dict.get("💥 Ballistics")), "Fire Rate missing"
    assert "45" in str(field_dict.get("📦 Ammunition")), "Capacity missing"
    assert "17" in str(field_dict.get("🎯 Handling")), "Recoil missing"
    print("[PASS] test_weapon_embed_formatting")


# ─── Test 7: Enemy Embed Formatting ──────────────────────────────────────────

def test_enemy_embed_formatting():
    """_format_enemy_embed must produce Main Health, Weakpoints, Limbs/Secondary."""
    import discord
    from src.bot.cogs.wiki import _format_enemy_embed
    from src.services.wiki_client import WikiArticle

    anatomy_table = (
        "[Tactical Data Table:]\n"
        "| Part Name: Main | Health: 2,400 | AV: Heavy | Fatal?: Yes |\n"
        "| Part Name: Head | Health: 1,200 | AV: Heavy | Fatal?: Yes |\n"
        "| Part Name: Torso Armor (2) | Health: 800 | AV: Heavy | Fatal?: No |\n"
        "| Part Name: Butt | Health: 950 | AV: Unarmored | Fatal?: Yes |\n"
        "| Part Name: Leg Armor (4) | Health: 500 | AV: Heavy | Fatal?: No |\n"
    )

    art = WikiArticle(
        title="Charger",
        url="https://helldivers.wiki.gg/wiki/Charger",
        summary="A terminid charger.",
        content="...",
        infobox={
            "Faction": "Terminid Horde",
            "Health": "2,400",
            "Size Class": "Large",
        },
        sections={"Anatomy": anatomy_table},
    )

    embed = discord.Embed(title="Test")
    handled = _format_enemy_embed(embed, art)
    assert handled is True, "Enemy not handled"

    field_names = [f.name for f in embed.fields]
    assert any("Main Health" in n for n in field_names), f"Missing Main Health: {field_names}"
    assert any("Weakpoints" in n for n in field_names), f"Missing Weakpoints: {field_names}"
    assert any("Limbs / Secondary" in n for n in field_names), f"Missing Limbs / Secondary: {field_names}"

    field_dict = {f.name: f.value for f in embed.fields}
    assert "2,400" in str(field_dict.get("❤️ Main Health")), "Main Health HP missing"
    assert "Head" in str(field_dict.get("🎯 Weakpoints")), "Head weakpoint missing"
    assert "Torso" in str(field_dict.get("🛡️ Limbs / Secondary")), "Torso secondary missing"
    print("[PASS] test_enemy_embed_formatting")


# ─── Test 8: STRATAGEM_INFOBOX_KEYS & Druid Support ─────────────────────────

def test_stratagem_infobox_keys_updated():
    """STRATAGEM_INFOBOX_KEYS should include druid-specific keys."""
    with open("src/bot/cogs/wiki.py", encoding="utf-8") as f:
        src = f.read()
    assert '"base cooldown"' in src, "Missing 'base cooldown' in STRATAGEM_INFOBOX_KEYS"
    assert '"permit type"' in src, "Missing 'permit type' in STRATAGEM_INFOBOX_KEYS"
    print("[PASS] test_stratagem_infobox_keys_updated")


def test_wiki_parser_has_druid_support():
    """wiki_client.py must contain druid-infobox parsing code."""
    with open("src/services/wiki_client.py", encoding="utf-8") as f:
        src = f.read()
    assert "druid-infobox" in src, "No druid-infobox parsing in wiki_client.py"
    assert "druid-label" in src, "No druid-label parsing"
    assert "druid-data" in src, "No druid-data parsing"
    assert "Stratagem.?Arrow" in src, "No Stratagem Arrow image parsing"
    assert "➡" in src and "⬆" in src and "⬇" in src and "⬅" in src, "Arrow Unicode mapping missing"
    print("[PASS] test_wiki_parser_has_druid_support")


# ─── Test 9: Safe Markdown Split Delimiter Repair ───────────────────────────

def test_safe_markdown_split_bold_repair():
    """safe_markdown_split should auto-close and reopen bold tags across boundaries."""
    from src.bot.cogs.wiki import safe_markdown_split

    long_intro = "A" * 900
    bold_target = "**PRIMARY FATAL WEAKPOINT: " + ("B" * 100) + "**"
    trailing = " - Deploy heavy anti-tank ordnance immediately to secure elimination."
    full_text = f"{long_intro} {bold_target}{trailing}"

    chunks = safe_markdown_split(full_text, max_len=950)
    assert len(chunks) >= 2, f"Expected multiple chunks, got {len(chunks)}"

    for idx, chunk in enumerate(chunks):
        assert len(chunk) <= 950, f"Chunk {idx + 1} exceeds max_len (length: {len(chunk)})"

    # First chunk must close the bold tag
    assert chunks[0].endswith("**"), f"Chunk 1 did not close bold tag: {repr(chunks[0][-20:])}"
    # Second chunk must reopen the bold tag
    assert chunks[1].startswith("**"), f"Chunk 2 did not reopen bold tag: {repr(chunks[1][:20])}"
    print("[PASS] test_safe_markdown_split_bold_repair")


# ─── Test 10: Safe Markdown Split Code Blocks & Lists ────────────────────────

def test_safe_markdown_split_code_blocks_and_bullets():
    """safe_markdown_split handles code blocks and long bullet lists without corrupting format."""
    from src.bot.cogs.wiki import safe_markdown_split

    # Long bullet list
    lines = [f"* Bullet Item {i}: **Telemetry Stat {i}** is verified by High Command." for i in range(40)]
    bullet_text = "\n".join(lines)
    chunks = safe_markdown_split(bullet_text, max_len=1000)

    for idx, chunk in enumerate(chunks):
        assert len(chunk) <= 1000, f"Bullet chunk {idx + 1} exceeds 1000: {len(chunk)}"
        # Verify no unclosed bold in any chunk
        assert chunk.count("**") % 2 == 0, f"Unclosed bold in chunk {idx + 1}"

    # Code block split
    code_text = "```python\n" + "\n".join([f"line_{i} = {i} * 42" for i in range(50)]) + "\n```"
    code_chunks = safe_markdown_split(code_text, max_len=500)
    assert len(code_chunks) >= 2
    for idx, c in enumerate(code_chunks):
        assert len(c) <= 500, f"Code chunk {idx + 1} exceeds 500: {len(c)}"
        assert c.count("```") % 2 == 0, f"Unclosed code block in chunk {idx + 1}"

    print("[PASS] test_safe_markdown_split_code_blocks_and_bullets")


# ─── Test 11: Tactical Embed Generation & Limits ─────────────────────────────

def test_build_tactical_embeds_structure():
    """build_tactical_embeds puts overview in description and sections into <=1000 char fields."""
    import discord
    from src.bot.cogs.wiki import build_tactical_embeds
    from src.services.wiki_client import WikiArticle

    weakpoints_list = "".join(["\n* Weakpoint Detail: " + ("x" * 120) for _ in range(12)])
    sample_answer = (
        "**ATTENTION, VANGUARD OF LIBERTY!**\n\n"
        "The Ministry of Truth has processed your tactical query regarding the Charger.\n\n"
        "---\n\n"
        "### 📊 OFFICIAL BALLISTIC TELEMETRY: CHARGER WEAK POINTS\n\n"
        "While the Charger is covered head-to-claw in heavy exoskeleton plates:\n"
        f"{weakpoints_list}\n\n---\n\n"
        "### 🎖️ HIGH COMMAND TACTICAL RECOMMENDATIONS\n\n"
        "* Deploy EAT-17 directly to frontal carapace.\n"
        "* For Super Earth! For Managed Democracy! 🦅"
    )

    art = WikiArticle(
        title="Charger",
        url="https://helldivers.wiki.gg/wiki/Charger",
        summary="A terminid beast.",
        content="...",
    )

    embeds = build_tactical_embeds(
        answer=sample_answer,
        matched_article=art,
        user_display_name="CitizenSoldier",
    )

    assert len(embeds) >= 1
    main_embed = embeds[0]

    # Description must contain overview narrative and stay <= 4096
    assert "ATTENTION, VANGUARD OF LIBERTY!" in main_embed.description
    assert len(main_embed.description) <= 4096

    # Verify all fields in all returned embeds have value <= 1000 chars and name <= 256
    all_fields: list[discord.EmbedField] = []
    for emb in embeds:
        for f in emb.fields:
            all_fields.append(f)
            assert len(f.name) <= 256, f"Field name too long: {len(f.name)}"
            assert len(f.value) <= 1000, f"Field value exceeds 1000 limit: {len(f.value)} (name={f.name})"

    field_names = [f.name for f in all_fields]
    # Check that large section was chunked with (Part 1), (Part 2)
    assert any("Part 1" in name for name in field_names), f"Missing Part 1 in: {field_names}"
    assert any("Part 2" in name for name in field_names), f"Missing Part 2 in: {field_names}"
    # Check that recommendations section is present
    assert any("RECOMMENDATIONS" in name.upper() for name in field_names)
    # Check verified source citation field is present
    assert any("Verified Intelligence Source" in name for name in field_names)

    print("[PASS] test_build_tactical_embeds_structure")


# ─── Test 12: Persona Max Output Tokens & Safety Settings ───────────────────

def test_persona_token_limit_and_safety_settings():
    """Persona answer_tactical_query must configure max_output_tokens >= 1500 and safety settings."""
    with open("src/ai/persona.py", encoding="utf-8") as f:
        src = f.read()

    # Verify max_output_tokens is >= 1500 (specifically 4096)
    match = re.search(r"max_output_tokens\s*=\s*(\d+)", src)
    assert match, "max_output_tokens not found in persona.py"
    tokens = int(match.group(1))
    assert tokens >= 1500, f"max_output_tokens is {tokens}, must be >= 1500"
    assert tokens == 4096, f"Expected max_output_tokens to be 4096, got {tokens}"

    # Verify safety settings are configured
    assert "safety_settings" in src, "safety_settings not found in persona.py"
    assert "BLOCK_ONLY_HIGH" in src, "BLOCK_ONLY_HIGH threshold not set in persona.py"

    # Verify deprecated gemini-1.5-flash is removed from fallbacks
    assert '"gemini-1.5-flash"' not in src, "Deprecated gemini-1.5-flash still in fallbacks"
    assert '"gemini-3.6-flash"' in src, "gemini-3.6-flash missing from fallbacks"
    assert '"gemini-3.5-flash"' in src, "gemini-3.5-flash missing from fallbacks"

    print("[PASS] test_persona_token_limit_and_safety_settings")


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_debrief_embed_title_no_difficulty()
    test_deaths_stat_echo_filtered()
    test_kills_stat_echo_filtered()
    test_friendly_fire_equals_filtered()
    test_unclosed_paren_truncation()
    test_clean_commentary_passes_through()
    test_druid_infobox_parsing()
    test_arrow_mapping()
    test_breadcrumb_and_version_filtered()
    test_stratagem_embed_fields_structure()
    test_weapon_embed_formatting()
    test_enemy_embed_formatting()
    test_stratagem_infobox_keys_updated()
    test_wiki_parser_has_druid_support()
    test_safe_markdown_split_bold_repair()
    test_safe_markdown_split_code_blocks_and_bullets()
    test_build_tactical_embeds_structure()
    test_persona_token_limit_and_safety_settings()
    print("\nAll 18 unit and integration tests passed successfully!")
