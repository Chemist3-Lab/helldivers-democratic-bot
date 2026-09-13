"""Markdown utilities for Discord message chunking and delimiter balancing.

Provides:
- safe_markdown_split: Chunk text safely up to Discord limits without breaking markdown syntax.
- parse_tactical_sections: Split formatted markdown documents into overview and titled sections.
- _get_unclosed_markdown_tags: Balance markdown delimiters across boundary cuts.
"""

from __future__ import annotations

import re


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
