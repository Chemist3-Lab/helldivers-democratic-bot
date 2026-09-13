"""Data models for MediaWiki search results and processed articles.

Defines:
- WikiSearchResult: Search query result with title, url, snippet, section anchor.
- WikiArticle: Fully parsed article with infobox, clean sections, and tactical brief generator.
"""

from __future__ import annotations

import time
from pydantic import BaseModel, Field


class WikiSearchResult(BaseModel):
    """Result of a search query on the MediaWiki instance."""

    title: str
    url: str
    description: str = Field(default="")
    section: str | None = Field(default=None, description="Matched section anchor if found in full-text search.")


class WikiArticle(BaseModel):
    """Processed wiki article ready for tactical terminal consumption."""

    title: str
    url: str
    summary: str
    content: str
    quote: str = Field(default="", description="In-game tactical description quote if available.")
    infobox: dict[str, str] = Field(default_factory=dict)
    sections: dict[str, str] = Field(default_factory=dict)
    target_section: str | None = Field(default=None, description="Prioritized section anchor from search query.")
    fetched_at: float = Field(default_factory=time.time)

    def get_tactical_brief(self, target_section: str | None = None, max_chars: int = 30000) -> str:
        """Combine infobox and clean sections into a structured tactical brief.

        If target_section is provided (or set on the article), that section and its
        adjacent tables are prioritized at the top of the dossier.
        """
        active_target = target_section or self.target_section
        lines = [f"# Tactical Dossier: {self.title}", f"Source: {self.url}\n"]
        if self.summary:
            lines.append(f"## Executive Summary\n{self.summary}\n")

        if self.infobox:
            lines.append("## Specifications & Technical Data")
            for k, v in self.infobox.items():
                lines.append(f"- **{k}**: {v}")
            lines.append("")

        prioritized_sections: list[tuple[str, str]] = []
        remaining_sections: list[tuple[str, str]] = []

        for sec_title, sec_content in self.sections.items():
            if not sec_content.strip():
                continue
            if active_target and (
                active_target.lower() in sec_title.lower()
                or sec_title.lower() in active_target.lower()
            ):
                prioritized_sections.append((sec_title, sec_content.strip()))
            else:
                remaining_sections.append((sec_title, sec_content.strip()))

        # Prioritized section(s) appear first
        for sec_title, sec_content in prioritized_sections:
            lines.append(f"## {sec_title} [TARGETED INTEL]\n{sec_content}\n")

        # Remaining sections in standard wiki order
        for sec_title, sec_content in remaining_sections:
            lines.append(f"## {sec_title}\n{sec_content}\n")

        full = "\n".join(lines)
        return full[:max_chars]

    @property
    def full_tactical_brief(self) -> str:
        """Combine infobox and clean sections into a structured tactical brief."""
        return self.get_tactical_brief()
