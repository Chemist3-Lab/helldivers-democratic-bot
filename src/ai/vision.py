"""Gemini Flash Vision Extraction Pipeline for Helldivers 2 Scoreboards.

Uses the `google-genai` SDK (`google.genai.Client`) with structured outputs
to analyze debrief extraction screenshots and extract per-player statistics:
- Player name
- Kills
- Deaths
- Stims used
- Accuracy %
- Friendly Fire damage
- Difficulty level

Includes strict Pydantic validation via `ScoreboardExtraction` and retry logic.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from typing import Any
from pydantic import BaseModel, Field, field_validator
from google import genai
from google.genai import types

from src.services._retry import async_retry

log = logging.getLogger(__name__)


# ─── Pydantic Extraction Models ──────────────────────────────────────────────


class PlayerExtraction(BaseModel):
    """Extracted metrics for an individual Helldiver from the mission end-screen."""

    name: str = Field(..., description="Player gamertag or ship name as visible.")
    kills: int = Field(default=0, ge=0, description="Total enemy kills.")
    deaths: int = Field(default=0, ge=0, description="Deaths suffered during mission.")
    stims_used: int = Field(default=0, ge=0, description="Number of stim packs consumed.")
    accuracy_pct: float = Field(default=0.0, ge=0.0, le=100.0, description="Shots hit percentage.")
    friendly_fire_dmg: float = Field(default=0.0, ge=0.0, description="Accidental damage to allies.")
    difficulty: int = Field(default=1, ge=1, le=10, description="Mission difficulty rating.")

    @field_validator("accuracy_pct", mode="before")
    @classmethod
    def parse_accuracy(cls, v: Any) -> float:
        """Parse accuracy string if returned with percentage symbol or int."""
        if isinstance(v, str):
            v = v.replace("%", "").strip()
        try:
            val = float(v)
            if val < 0:
                return 0.0
            if val > 100:
                return 100.0
            return val
        except (ValueError, TypeError):
            return 0.0

    @field_validator("kills", "deaths", "stims_used", mode="before")
    @classmethod
    def parse_integers(cls, v: Any) -> int:
        """Handle negative sentinel values or formatting strings."""
        try:
            val = int(float(v))
            return max(val, 0)
        except (ValueError, TypeError):
            return 0

    @field_validator("friendly_fire_dmg", mode="before")
    @classmethod
    def parse_float_dmg(cls, v: Any) -> float:
        try:
            val = float(v)
            return max(val, 0.0)
        except (ValueError, TypeError):
            return 0.0


class ScoreboardExtraction(BaseModel):
    """Top-level container for all player metrics in a scoreboard screenshot."""

    difficulty: int = Field(default=1, ge=1, le=10, description="Mission difficulty level.")
    mission_success: bool = Field(default=True, description="Whether the extraction/mission succeeded.")
    players: list[PlayerExtraction] = Field(
        default_factory=list,
        description="List of extracted player stats from the scoreboard.",
    )


# ─── Vision Extraction Engine ────────────────────────────────────────────────


VISION_SYSTEM_INSTRUCTION = """You are an automated Super Earth Military Intelligence Vision Processing Unit.
Analyze this Helldivers 2 post-mission extraction debrief screenshot.
Extract the stats for EVERY player listed on the scoreboard table.

Metrics to extract per player:
- name: Player username / callsign
- kills: Total kills count
- deaths: Times died
- stims_used: Stims used
- accuracy_pct: Shot accuracy percentage (e.g. 72.5)
- friendly_fire_dmg: Friendly fire damage count
- difficulty: The difficulty level of the mission (1-10, e.g. Trivial=1, Helldive=9, Super Helldive=10). If uncertain, default to 7.

Ensure accurate numbers. Return only data matching the required schema."""


class ScoreboardVisionExtractor:
    """Async vision extractor using the google-genai SDK."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
    ) -> None:
        self.api_key = api_key
        self.model_name = model_name
        self._client: genai.Client | None = None

    @property
    def client(self) -> genai.Client:
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    @async_retry(max_retries=3, base_delay=2.0, backoff_factor=2.0)
    async def extract_scoreboard(
        self,
        image_bytes: bytes,
        mime_type: str = "image/png",
    ) -> ScoreboardExtraction:
        """Process screenshot bytes with Gemini Flash and validate into ScoreboardExtraction.

        Uses the google-genai async client (`aio.models.generate_content`) with
        response_mime_type="application/json" and response_schema.
        """
        # Form the image part using types.Part.from_bytes
        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=mime_type,
        )

        prompt = "Extract all player statistics and mission difficulty from this Helldivers 2 scoreboard."

        config = types.GenerateContentConfig(
            system_instruction=VISION_SYSTEM_INSTRUCTION,
            temperature=0.1,
            response_mime_type="application/json",
            response_schema=ScoreboardExtraction,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=[prompt, image_part],
            config=config,
        )

        raw_text = response.text or "{}"
        try:
            parsed = ScoreboardExtraction.model_validate_json(raw_text)
        except Exception as e:
            # Fallback if response came with markdown code fences
            cleaned = re.sub(r"^```(?:json)?\n|\n```$", "", raw_text.strip(), flags=re.MULTILINE)
            try:
                parsed = ScoreboardExtraction.model_validate_json(cleaned)
            except Exception:
                log.error("Failed to parse vision response as ScoreboardExtraction: %s", raw_text)
                raise ValueError(f"Could not parse scoreboard extraction: {e}") from e

        # Propagate top-level difficulty to players if individual difficulty is missing or default
        for p in parsed.players:
            if p.difficulty == 1 and parsed.difficulty > 1:
                p.difficulty = parsed.difficulty

        return parsed
