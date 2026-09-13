"""Unit tests for newly extracted modular components and presentation layer."""

import os
import sys
import pytest
from bs4 import BeautifulSoup

# Ensure project root is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.bot.cogs.scoreboard import _resolve_deterministic_id
from src.bot.ui.markdown import _get_unclosed_markdown_tags, parse_tactical_sections, safe_markdown_split
from src.bot.ui.scoreboard_embeds import calculate_dvr_rank
from src.bot.ui.war_embeds import resolve_major_order_objectives
from src.services.hd2_api import Campaign, MajorOrder, PlanetInfo
from src.services.wiki_models import WikiArticle
from src.services.wiki_parser import clean_soup_html, format_table_markdown


def test_format_table_markdown():
    """format_table_markdown must convert HTML table to clean markdown key-values."""
    html = """
    <table>
        <tr><th>Part Name</th><th>Health [1]</th><th>AV</th><th>Fatal?</th></tr>
        <tr><td>Head</td><td>1,500</td><td>Heavy</td><td>Yes</td></tr>
        <tr><td>Body</td><td>4,000</td><td>Medium</td><td>No</td></tr>
    </table>
    """
    soup = BeautifulSoup(html, "html.parser")
    lines = format_table_markdown(soup.find("table"))
    assert len(lines) == 2
    assert "Part Name: Head" in lines[0]
    assert "Health: 1,500" in lines[0]
    assert "AV: Heavy" in lines[0]
    assert "Fatal?: Yes" in lines[0]


def test_clean_soup_html_parser_isolated():
    """clean_soup_html must parse infoboxes, quotes, and sections into a WikiArticle."""
    sample_html = """
    <div class="mw-parser-output">
        <blockquote><p>An explosive orbital munition for Super Earth democracy.</p></blockquote>
        <div class="druid-infobox">
            <div class="druid-row">
                <div class="druid-label">Call-in Time</div>
                <div class="druid-data">2 sec</div>
            </div>
            <div class="druid-row">
                <div class="druid-label">Stratagem Code</div>
                <div class="druid-data">
                    <img alt="Stratagem Arrow Right" src="arrow_right.png"/>
                    <img alt="Stratagem Arrow Up" src="arrow_up.png"/>
                    <img alt="Stratagem Arrow Down" src="arrow_down.png"/>
                </div>
            </div>
        </div>
        <p>Overview text describing orbital railcannon strikes.</p>
        <h2>Tactical Specs</h2>
        <p>Heavy armor penetration projectile.</p>
    </div>
    """
    article = clean_soup_html(sample_html, "Orbital Railcannon Strike", "https://helldivers.wiki.gg/wiki/Orbital_Railcannon_Strike")
    assert article.title == "Orbital Railcannon Strike"
    assert "explosive orbital munition" in article.quote
    assert article.infobox.get("Call-in Time") == "2 sec"
    assert "➡ ⬆ ⬇" in article.infobox.get("Stratagem Code", "")
    assert "Tactical Specs" in article.sections


def test_markdown_delimiter_balancing():
    """_get_unclosed_markdown_tags must identify unbalanced formatting tags."""
    text_bold = "This is **unclosed bold"
    suffix, prefix = _get_unclosed_markdown_tags(text_bold)
    assert suffix == "**"
    assert prefix == "**"

    text_code = "```python\ndef test():"
    suffix_code, prefix_code = _get_unclosed_markdown_tags(text_code)
    assert "\n```" in suffix_code
    assert "```\n" in prefix_code


def test_safe_markdown_split_preserves_content():
    """safe_markdown_split must split long markdown text cleanly."""
    long_text = "**Section 1:** " + ("Information regarding Super Earth weapons and galactic defense. " * 30)
    chunks = safe_markdown_split(long_text, max_len=300)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 350
        # Ensure tags are balanced in each chunk
        assert chunk.count("**") % 2 == 0


def test_parse_tactical_sections():
    """parse_tactical_sections must split text into overview narrative and sections."""
    doc = (
        "Super Earth High Command Strategic Overview.\n\n"
        "## Weapons Telemetry\nStandard Liberator delivers 60 ballistic damage per round.\n\n"
        "## Enemy Profiles\nChargers possess heavily armored front carapaces."
    )
    overview, sections = parse_tactical_sections(doc)
    assert "Super Earth High Command Strategic Overview" in overview
    assert len(sections) == 2
    assert sections[0][0] == "Weapons Telemetry"
    assert "60 ballistic damage" in sections[0][1]
    assert sections[1][0] == "Enemy Profiles"


def test_calculate_dvr_rank():
    """calculate_dvr_rank must return patriotic titles corresponding to DVR thresholds."""
    assert calculate_dvr_rank(350.0) == "⭐⭐⭐⭐⭐ Super Citizen"
    assert calculate_dvr_rank(220.0) == "⭐⭐⭐⭐ Elite Helldiver"
    assert calculate_dvr_rank(130.0) == "⭐⭐⭐ Veteran Helldiver"
    assert calculate_dvr_rank(60.0) == "⭐⭐ Helldiver"
    assert calculate_dvr_rank(10.0) == "⭐ Cadet"


def test_deterministic_id_resolution():
    """_resolve_deterministic_id must generate stable, repeatable synthetic snowflakes."""
    id1 = _resolve_deterministic_id("JohnHelldiver")
    id2 = _resolve_deterministic_id("johnhelldiver")
    id3 = _resolve_deterministic_id("AnotherPlayer")

    assert id1 == id2, "ID should be case-insensitive and identical across calls"
    assert id1 != id3, "Different player names should produce different IDs"
    assert isinstance(id1, int)
    assert id1 >= 10**16, "Should be in high synthetic snowflake range"


def test_resolve_major_order_objectives():
    """resolve_major_order_objectives must resolve planet names and statuses."""
    order = MajorOrder(
        id=101,
        title="Liberate Charbal-VII",
        tasks=[
            {"values": [42], "valueTypes": [12]},
            {"values": [99], "valueTypes": [12]},
        ],
        progress=[1, 0],
    )
    planets_map = {42: "Charbal-VII", 99: "Zzaniah Prime"}
    campaigns = [
        Campaign(
            id=1,
            planet=PlanetInfo(index=99, name="Zzaniah Prime", maxHealth=1000000, currentHealth=500000),
            type=1,
        )
    ]

    objectives = resolve_major_order_objectives(order, planets_map, campaigns)
    assert len(objectives) == 2
    assert "✅ **Charbal-VII** — Liberated" in objectives[0]
    assert "⏳ **Zzaniah Prime** — In Progress" in objectives[1]
