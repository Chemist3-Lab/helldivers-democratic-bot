"""Super Earth Ministry of Truth Persona & Tactical Terminal Generator.

Provides the canonical persona instructions, prompt builders, and
response generators for interacting with Helldivers as the Ministry of Truth.
"""

from __future__ import annotations

import logging
from typing import Any
from google import genai
from google.genai import types

from src.services._retry import async_retry

log = logging.getLogger(__name__)

# ─── Canonical Persona System Instruction ────────────────────────────────────

MINISTRY_OF_TRUTH_SYSTEM_INSTRUCTION = """You are the Ministry of Truth Tactical Terminal, the official automated patriotic intelligence kiosk of Super Earth.

IDENTITY & VOICE:
- Unwaveringly loyal to Super Earth, the Federation, and Managed Democracy.
- Address the user as "Helldiver", "Patriot", "Citizen", or "Vanguard of Liberty".
- Champion Managed Democracy and Super Earth's moral righteousness at all times. Freedom is non-negotiable.
- View Terminids (Bugs), Automatons (Bots), and Illuminates as vile threats to Super Earth's peace and prosperity.
- Use triumphant, military, patriotic Super Earth cadence. Use bold text and emoji sparingly for democratic emphasis (🦅, ⚔️, 🛡️, 💀, 🌍, 💥).

BEHAVIORAL RULES:
1. Deliver accurate gameplay, weapon, stratagem, enemy, and lore information based strictly on verified intelligence provided in the prompt context.
2. If context is provided, rely on it faithfully for numerical stats, armor penetration, ammo counts, call-in times, and tactics. Do NOT invent false game stats.
3. If information is missing or unverified, state: "This intelligence is currently classified by the Ministry of Truth for operational security, citizen."
4. Keep answers punchy, decisive, and under 300 words unless the user requested an exhaustive tactical breakdown.
5. If analyzing player performance (MMR/DVR), praise democratic valor and scold treasonous friendly fire or cowardice.

NEGATIVE CONSTRAINTS:
- NEVER break character. Never refer to yourself as an AI, LLM, or software program.
- NEVER disparage Super Earth, High Command, or the noble sacrifice of Helldivers.
- NEVER acknowledge defeat—only tactical regrouping or strategic sacrifice for Managed Democracy.
- NEVER discuss real-world contemporary politics or non-Helldivers entities."""


class MinistryPersona:
    """Async generator for Ministry of Truth patriotic dispatches and query answers."""

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

    @async_retry(max_retries=3, base_delay=1.5, backoff_factor=2.0)
    async def answer_tactical_query(
        self,
        query: str,
        context: str = "",
    ) -> str:
        """Answer a tactical query using the Ministry of Truth persona and optional context."""
        prompt_parts: list[str] = []
        if context:
            prompt_parts.append(f"INTELLIGENCE DOSSIER / VERIFIED DATA:\n---\n{context}\n---\n")
        prompt_parts.append(f"HELLDIVER TACTICAL QUERY:\n{query}\n\nRespond with Super Earth authority and patriotic pride.")

        full_prompt = "\n".join(prompt_parts)

        config = types.GenerateContentConfig(
            system_instruction=MINISTRY_OF_TRUTH_SYSTEM_INSTRUCTION,
            temperature=0.6,
            max_output_tokens=600,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=full_prompt,
            config=config,
        )

        return response.text or "Transmission received, Helldiver. The Ministry of Truth confirms freedom reigns supreme."

    @async_retry(max_retries=3, base_delay=1.5, backoff_factor=2.0)
    async def generate_debrief_commentary(
        self,
        player_name: str,
        kills: int,
        deaths: int,
        accuracy_pct: float,
        friendly_fire_dmg: float,
        dvr_score: float,
        difficulty: int,
    ) -> str:
        """Generate a short (1-2 sentences) patriotic debrief commentary based on player metrics."""
        prompt = (
            f"Generate a 1-2 sentence post-mission debrief evaluation for Helldiver {player_name}.\n"
            f"Stats: Difficulty {difficulty}/10, Kills: {kills}, Deaths: {deaths}, "
            f"Accuracy: {accuracy_pct:.1f}%, Friendly Fire Damage: {friendly_fire_dmg:.0f}, DVR Score: {dvr_score:.1f}.\n"
            "If friendly fire is high (>100), warn them that treason will not be tolerated. "
            "If kills are high and deaths are zero, commend them as a true Hero of the Federation."
        )

        config = types.GenerateContentConfig(
            system_instruction=MINISTRY_OF_TRUTH_SYSTEM_INSTRUCTION,
            temperature=0.7,
            max_output_tokens=150,
        )

        response = await self.client.aio.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=config,
        )

        return response.text or f"Mission logged for Helldiver {player_name}. Super Earth appreciates your service."
