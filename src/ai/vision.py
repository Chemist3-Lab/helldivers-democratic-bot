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

    mission_success: bool = Field(default=True, description="Whether the extraction/mission succeeded.")
    players: list[PlayerExtraction] = Field(
        default_factory=list,
        description="List of extracted player stats from the scoreboard.",
    )


# ─── Vision Extraction Engine ────────────────────────────────────────────────


VISION_SYSTEM_INSTRUCTION = """You are an automated Super Earth Military Intelligence Vision Processing Unit.
Analyze this Helldivers 2 post-mission extraction debrief screenshot.
Extract the telemetry stats for EVERY player listed on the scoreboard table.

IMPORTANT NOTE ON DIFFICULTY:
- Helldivers 2 post-mission scoreboards DO NOT display mission difficulty. Do NOT attempt to guess, estimate, or extract difficulty.

Strict Schema & Bounding Constraints:
- Return ONLY valid JSON strictly conforming to the ScoreboardExtraction schema.
- mission_success: Boolean indicating if the squad extracted or completed the primary mission (true if Victory/Extracted, false if Defeat/MIA).
- players: Array containing an entry for every distinct Helldiver on the scoreboard with:
  * name: Player gamertag / callsign exactly as visible on screen.
  * kills: Non-negative integer (>= 0).
  * deaths: Non-negative integer (>= 0).
  * stims_used: Non-negative integer (>= 0).
  * accuracy_pct: Floating point percentage bounded strictly between 0.0 and 100.0 (e.g. 78.5). Do NOT include the '%' character.
  * friendly_fire_dmg: Non-negative floating point or integer damage dealt to teammates (>= 0.0).

All numerical stats must be non-negative. Do not fabricate missing rows."""


class ScoreboardVisionExtractor:
    """Async vision extractor using the google-genai SDK."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-3.6-flash",
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

        models_to_try = [self.model_name]
        for fallback in ("gemini-3.6-flash", "gemini-1.5-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"):
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        response = None
        last_err: Exception | None = None
        for model in models_to_try:
            try:
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=[prompt, image_part],
                    config=config,
                )
                break
            except Exception as exc:
                last_err = exc
                err_str = str(exc)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "404" in err_str or "NOT_FOUND" in err_str:
                    log.warning("Vision model %s encountered quota/not-found error, trying next fallback: %s", model, exc)
                    continue
                raise

        if response is None:
            if last_err:
                raise last_err
            raise RuntimeError("Vision model generation failed without returning a response.")

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
        return parsed
