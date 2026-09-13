"""HTML and DOM parser for helldivers.wiki.gg MediaWiki articles.

Extracts structured data, infoboxes, tactical data tables, and in-game quotes
from raw HTML using BeautifulSoup. This module is purely computational and
contains no network I/O or caching logic.
"""

from __future__ import annotations

import logging
import re
from bs4 import BeautifulSoup

from src.services.wiki_models import WikiArticle

log = logging.getLogger(__name__)

# Arrow symbols mapping for directional input codes
ARROW_MAP = {"Right": "➡", "Up": "⬆", "Down": "⬇", "Left": "⬅"}


def format_table_markdown(table: BeautifulSoup) -> list[str]:
    """Format an HTML table (wikitable, infobox, stats-table) into structured markdown key-value pairs.

    Handles header extraction, footnote stripping, and formats cells cleanly:
    e.g., '| Part Name: Head | Health: 1,500 | AV: Heavy | Location: Front Side | Fatal?: Yes |'
    """
    rows = table.find_all("tr")
    if not rows:
        return []

    headers: list[str] = []
    header_row = table.find("tr")
    if header_row:
        for th in header_row.find_all(["th", "td"]):
            h_text = th.get_text(" ", strip=True)
            # Remove bracketed footnotes and trailing footnote numbers (e.g. 'Health 1' -> 'Health')
            h_text = h_text.split("[")[0].strip()
            h_text = re.sub(r"\s+\d+$", "", h_text)
            headers.append(h_text)

    output_lines: list[str] = []
    # If the first row had headers, skip it for data rows
    data_rows = rows[1:] if any(row.find_all("th") for row in rows[:1]) else rows

    for row in data_rows:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if not cells or all(not c for c in cells):
            continue

        row_pairs: list[str] = []
        for idx, cell in enumerate(cells):
            if not cell:
                continue
            header_name = headers[idx] if idx < len(headers) and headers[idx] else f"Col_{idx+1}"
            row_pairs.append(f"{header_name}: {cell}")

        if row_pairs:
            output_lines.append(f"| {' | '.join(row_pairs)} |")

    return output_lines


def clean_soup_html(html_text: str, page_title: str, page_url: str) -> WikiArticle:
    """Parse raw MediaWiki HTML into a structured WikiArticle using BeautifulSoup."""
    soup = BeautifulSoup(html_text, "html.parser")

    # Remove script, style, nav, edit links, references, table of contents (toc)
    for tag in soup(["script", "style", "noscript", ".mw-editsection", ".navbox", ".reference", "#toc", ".toc"]):
        tag.decompose()

    # Remove version and patch notice boxes (e.g. "Last updated: 1.006.300 Current patch: 1.007.002")
    for box in soup.find_all(["div", "table"]):
        classes = box.get("class") or []
        if any("druid" in c for c in classes):
            continue
        txt = box.get_text().lower().strip()
        if (txt.startswith("last updated:") or "current patch:" in txt) and len(txt) < 200:
            box.decompose()

    # Extract in-game tactical description quote if present (e.g. from blockquote)
    quote_text = ""
    quote_elem = soup.find("blockquote")
    if quote_elem:
        for p in quote_elem.find_all("p"):
            pt = p.get_text(" ", strip=True)
            if pt and not pt.startswith("—") and not pt.startswith("-") and len(pt) > 10:
                quote_text = pt
                break

    infobox_data: dict[str, str] = {}
    infobox = soup.find(class_=re.compile(r"portable-infobox|infobox"))
    if infobox:
        # Extract key-value rows
        for row in infobox.find_all("tr"):
            th = row.find(["th", "td"], class_=re.compile(r"pi-data-label|label"))
            td = row.find(["td"], class_=re.compile(r"pi-data-value|value"))
            if th and td:
                key = th.get_text(strip=True)
                val = td.get_text(" ", strip=True)
                if key and val:
                    infobox_data[key] = val

        # Handle portable infobox items
        for item in infobox.find_all(class_="pi-item"):
            label = item.find(class_="pi-data-label")
            val = item.find(class_="pi-data-value")
            if label and val:
                key = label.get_text(strip=True)
                v = val.get_text(" ", strip=True)
                if key and v:
                    infobox_data[key] = v

    # Also parse druid-style infoboxes (helldivers.wiki.gg uses druid-infobox, not portable-infobox)
    druid_infobox = soup.find(class_=re.compile(r"druid-infobox|druid-container"))
    if druid_infobox:
        for row in druid_infobox.find_all(class_=re.compile(r"druid-row")):
            label_el = row.find(class_=re.compile(r"druid-label"))
            data_el = row.find(class_=re.compile(r"druid-data"))
            if not label_el or not data_el:
                continue
            key = label_el.get_text(strip=True)
            if not key:
                continue
            # Special handling for stratagem arrow codes (images with no text)
            arrow_imgs = data_el.find_all("img", alt=re.compile(r"Stratagem.?Arrow", re.IGNORECASE))
            if arrow_imgs:
                arrows: list[str] = []
                for img in arrow_imgs:
                    alt = img.get("alt", "")
                    for direction, symbol in ARROW_MAP.items():
                        if direction.lower() in alt.lower():
                            arrows.append(symbol)
                            break
                val = " ".join(arrows) if arrows else data_el.get_text(" ", strip=True)
            else:
                val = data_el.get_text(" ", strip=True)
            if val:
                infobox_data[key] = val

    # Also extract standard wikitable infobox tables (th/td key-value rows)
    if not infobox_data:
        for tbl in soup.find_all("table", class_=re.compile(r"infobox")):
            for row in tbl.find_all("tr"):
                th = row.find("th")
                td = row.find("td")
                if th and td:
                    key = th.get_text(strip=True)
                    # Handle arrow images in td
                    arrow_imgs = td.find_all("img", alt=re.compile(r"Stratagem.?Arrow|Arrow", re.IGNORECASE))
                    if arrow_imgs and not td.get_text(strip=True):
                        arrows_fb: list[str] = []
                        for img in arrow_imgs:
                            alt = img.get("alt", "")
                            for d, s in ARROW_MAP.items():
                                if d.lower() in alt.lower():
                                    arrows_fb.append(s)
                                    break
                        val = " ".join(arrows_fb) if arrows_fb else ""
                    else:
                        val = td.get_text(" ", strip=True)
                    if key and val:
                        infobox_data[key] = val

    # Extract Call-in Time, Uses, and Cooldown from Stratagem Statistics or general wikitables
    for tbl in soup.find_all("table", class_=re.compile(r"wikitable")):
        for tr in tbl.find_all("tr"):
            th = tr.find("th")
            tds = tr.find_all("td")
            if th and tds:
                th_text = th.get_text(strip=True).lower()
                td_val = tds[0].get_text(" ", strip=True)
                if "call-in" in th_text and "Call-in Time" not in infobox_data:
                    infobox_data["Call-in Time"] = td_val
                elif th_text == "uses" and "Uses" not in infobox_data:
                    infobox_data["Uses"] = td_val
                elif "cooldown" in th_text and "Cooldown" not in infobox_data:
                    # In Stratagem Statistics, row may have td[0]='Standard', td[1]='180 seconds'
                    if len(tds) > 1 and any(char.isdigit() for char in tds[1].get_text()):
                        infobox_data["Cooldown"] = tds[1].get_text(" ", strip=True)
                    else:
                        infobox_data["Cooldown"] = td_val

    # Extract summary (paragraphs before first heading)
    summary_paras: list[str] = []
    content_div = soup.find(class_="mw-parser-output") or soup
    for elem in content_div.children:
        if elem.name in ("h2", "h3", "table"):
            break
        if elem.name == "p":
            text = elem.get_text(" ", strip=True)
            if text:
                summary_paras.append(text)
    summary = "\n\n".join(summary_paras)

    # Extract sections (including paragraphs, lists, card containers, and data tables)
    sections: dict[str, str] = {}
    current_section = "Overview"
    current_lines: list[str] = []

    for tag in content_div.find_all(["h2", "h3", "h4", "p", "ul", "ol", "table", "div"]):
        if tag.name in ("h2", "h3", "h4"):
            if current_lines:
                sections[current_section] = "\n".join(current_lines)
                current_lines = []
            clean_heading = re.sub(r"\[edit.*?\]", "", tag.get_text(strip=True)).strip()
            current_section = clean_heading
            # Ignore standard wiki boilerplate / non-tactical sections
            if current_section.lower() in (
                "references", "see also", "external links", "navigation", "gallery", "trivia", "change history"
            ):
                current_section = ""
        elif current_section:
            if tag.name == "p":
                t = tag.get_text(" ", strip=True)
                if t:
                    current_lines.append(t)
            elif tag.name in ("ul", "ol"):
                # Avoid processing nested list items multiple times
                if tag.find_parent(["ul", "ol"]):
                    continue
                for li in tag.find_all("li", recursive=False):
                    li_t = li.get_text(" ", strip=True)
                    if li_t:
                        current_lines.append(f"- {li_t}")
            elif tag.name == "table":
                # Avoid processing nested tables multiple times
                if tag.find_parent("table"):
                    continue
                table_classes = tag.get("class", [])
                is_data_table = any(
                    cls in ("wikitable", "infobox", "stats-table", "enemy-attack-stats")
                    for cls in table_classes
                ) or tag.find("th") is not None
                if is_data_table:
                    tbl_lines = format_table_markdown(tag)
                    if tbl_lines:
                        current_lines.append("\n[Tactical Data Table:]")
                        current_lines.extend(tbl_lines)
                        current_lines.append("")
            elif tag.name == "div":
                # Skip known noise containers that produce duplicate text
                div_classes = set(tag.get("class", []))
                noise_classes = {
                    "breadcrumb", "noexcerpt", "navbox", "ranger-navbox",
                    "toc", "tabs", "tab", "tab-spacer", "druid-infobox",
                    "druid-container", "mw-collapsible", "navigation-not-searchable",
                    "flextablediv",
                }
                if div_classes & noise_classes:
                    continue
                # Only handle card containers, link grids, or text divs that don't contain sub-blocks
                if tag.find_parent("table") or tag.find_parent(class_="Roundededges"):
                    continue
                if tag.find_parent(class_=re.compile(r"druid-infobox|druid-container|breadcrumb|navbox|tabs")):
                    continue
                if tag.find(["h2", "h3", "h4", "table", "p", "ul", "ol"]):
                    continue
                links = [
                    a.get_text(strip=True)
                    for a in tag.find_all("a")
                    if a.get_text(strip=True)
                ]
                links = [
                    link
                    for link in links
                    if not link.startswith("[") and not link.isdigit() and len(link) > 1
                ]
                if links:
                    for item in links:
                        if f"- {item}" not in current_lines:
                            current_lines.append(f"- {item}")
                else:
                    t = tag.get_text(" ", strip=True)
                    if t and len(t) > 3 and t not in current_lines:
                        current_lines.append(t)

    if current_section and current_lines:
        sections[current_section] = "\n".join(current_lines)

    # Clean text content
    clean_content = content_div.get_text("\n", strip=True)
    # Strip version tags (e.g. 1.004.000, 1.007.002) from summary
    summary = re.sub(r"\b\d+\.\d{3}\.\d{3}\b", "", summary).strip()

    return WikiArticle(
        title=page_title,
        url=page_url,
        summary=summary,
        quote=quote_text,
        content=clean_content,
        infobox=infobox_data,
        sections=sections,
    )


# Backward compatibility aliases
_clean_soup_html = clean_soup_html
_format_table_markdown = format_table_markdown
