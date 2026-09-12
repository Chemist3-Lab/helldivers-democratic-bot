"""Super Earth Ministry of Truth Persona & Tactical Terminal Generator.

Provides the canonical persona instructions, prompt builders, and
response generators for interacting with Helldivers as the Ministry of Truth.
"""

from __future__ import annotations

import logging
import re
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

SOURCE GROUNDING MANDATE:
1. You must NEVER use outdated pre-training knowledge to answer counts, stats, or game rosters. If the provided wiki text states 'All 24 Warbonds', you must state 24. Always cite the exact figures found in the provided context. If the provided context does not contain the answer, explicitly declare that High Command archives require a more specific query rather than inventing numbers.
2. High Command DEMANDS complete tactical transparency on enemy weakpoints, hit points, warbonds, and hardware metrics to optimize combat efficacy.
3. You MUST provide the exact numerical stats (HP values, armor penetration values, weakpoint health, roster counts, call-in times, cooldowns) retrieved from the tactical context. Frame these numbers proudly as "Official Ministry of Truth Combat Telemetry" or "Authorized Ballistic Intelligence".
4. CRITICAL PROTOCOL: Rely EXCLUSIVELY on the verified wiki context provided. If a specific variant exists in the context (e.g., Predator Stalker) but exact numerical HP is not specified in the database tables, state the verified behavioral and threat intel provided (e.g., mutated variant spawning in patrols, incapable of cloaking, persistent relentless pursuit) and explicitly note that exact ballistic hit-point data for this strain remains uncataloged in High Command's public archives. Never invent arbitrary HP values.
5. Deliver accurate gameplay, weapon, stratagem, enemy, and lore information based strictly on verified intelligence provided in the prompt context. If context contains data tables (e.g. Part Name, Health, Armor/AV, Fatal weakpoints), format and report those exact figures clearly.
6. Never refuse to disclose stats when they exist, never say hit points or numbers are confidential or forbidden to look up, and NEVER claim stats are classified. Knowledge is liberty's sharpest bayonet.
7. If analyzing player performance (MMR/DVR), praise democratic valor and scold treasonous friendly fire or cowardice.

NEGATIVE CONSTRAINTS:
- NEVER use outdated pre-training knowledge to answer counts, stats, or game rosters.
- NEVER invent arbitrary HP values, warbond counts, or fabricate stats not present in the dossier.
- NEVER refuse to disclose stats when they exist in context, and never claim that verified enemy health, armor, or weapon metrics are classified.
- NEVER tell a Helldiver not to research numbers or hit points; High Command demands informed soldiers.
- NEVER break character. Never refer to yourself as an AI, LLM, or software program.
- NEVER disparage Super Earth, High Command, or the noble sacrifice of Helldivers.
- NEVER acknowledge defeat—only tactical regrouping or strategic sacrifice for Managed Democracy.
- NEVER discuss real-world contemporary politics or non-Helldivers entities."""


class MinistryPersona:
    """Async generator for Ministry of Truth patriotic dispatches and query answers."""

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
            max_output_tokens=4096,
            safety_settings=[
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
            ],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        models_to_try = [self.model_name]
        for fallback in ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"):
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        last_err: Exception | None = None
        for model in models_to_try:
            try:
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=full_prompt,
                    config=config,
                )
                if response.candidates and response.candidates[0].finish_reason:
                    log.debug("Tactical query finish reason: %s (model: %s)", response.candidates[0].finish_reason, model)
                return response.text or "Transmission received, Helldiver. The Ministry of Truth confirms freedom reigns supreme."
            except Exception as exc:
                last_err = exc
                err_str = str(exc)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "404" in err_str or "NOT_FOUND" in err_str:
                    log.warning("Model %s encountered quota/not-found error, trying next fallback: %s", model, exc)
                    continue
                raise
        if last_err:
            raise last_err
        return "Transmission received, Helldiver. The Ministry of Truth confirms freedom reigns supreme."

    @async_retry(max_retries=3, base_delay=1.5, backoff_factor=2.0)
    async def generate_debrief_commentary(
        self,
        player_name: str,
        kills: int,
        deaths: int,
        accuracy_pct: float,
        friendly_fire_dmg: float,
        dvr_score: float,
        difficulty: int | None = None,
    ) -> str:
        """Generate a short (1-2 sentences) patriotic debrief commentary based on player metrics."""
        diff_str = f", Mission Difficulty: {difficulty}/10" if difficulty is not None else ""
        prompt = (
            f"You are the Super Earth Ministry of Truth evaluating a Helldiver's extraction telemetry.\n"
            f"Player Call-sign: {player_name}\n"
            f"Combat Metrics: Kills: {kills}, Deaths: {deaths}, Accuracy: {accuracy_pct:.1f}%, "
            f"Friendly Fire Damage: {friendly_fire_dmg:.0f}, Net DVR Score: {dvr_score:.1f}{diff_str}.\n\n"
            "TASK:\n"
            f"Write a concise, 1-2 sentence patriotic post-mission debrief evaluation addressed to Helldiver {player_name}.\n\n"
            "CRITICAL DIRECTIVES:\n"
            "- Speak directly with Super Earth military authority, patriotic pride, and democratic fervor.\n"
            "- Output ONLY the final spoken dialogue in character. NEVER output thinking, reasoning steps, math comparisons, or stat equations (e.g., do NOT write 'Friendly Fire = ...' or '(> 100)').\n"
            "- Speak in complete, natural patriotic sentences. NEVER echo raw stat labels like 'Deaths: X (' or 'Kills: Y ('.\n"
            "- If friendly fire is significant (>100), reprimand them sharply that reckless negligence borders on treason.\n"
            "- If kills are high and deaths are zero, praise them as an elite Hero of the Federation.\n"
            "- Keep the response complete, fully formed, and strictly under 400 characters. Never cut off mid-thought."
        )

        config = types.GenerateContentConfig(
            system_instruction=MINISTRY_OF_TRUTH_SYSTEM_INSTRUCTION,
            temperature=0.7,
            max_output_tokens=1000,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        models_to_try = [self.model_name]
        for fallback in ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-latest"):
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        raw_response_text = ""
        last_err: Exception | None = None
        for model in models_to_try:
            try:
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                if response.text:
                    raw_response_text = response.text
                    break
            except Exception as exc:
                last_err = exc
                err_str = str(exc)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "404" in err_str or "NOT_FOUND" in err_str:
                    log.warning("Model %s encountered quota/not-found error, trying next fallback: %s", model, exc)
                    continue
                raise

        # Post-process and sanitize commentary to guarantee clean, untruncated output
        cleaned = raw_response_text.strip()
        cleaned = re.sub(
            r"^(?:High Command Assessment|Ministry Assessment|Debrief|Evaluation|Assessment|Transmission):\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^\*+|\*+$", "", cleaned).strip()
        cleaned = re.sub(r"^\"+|\"+$", "", cleaned).strip()

        # Remove lines that look like leaked math/logic scrapings or stat echoing
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

        # Fix unclosed parentheses: if result ends mid-paren, truncate at last sentence before it
        if result.count("(") > result.count(")"):
            last_open = result.rfind("(")
            # Find last sentence boundary before the unclosed paren
            before_paren = result[:last_open].rstrip()
            sentence_end = max(before_paren.rfind("."), before_paren.rfind("!"), before_paren.rfind("?"))
            if sentence_end > 20:
                result = before_paren[: sentence_end + 1].strip()
            else:
                result = before_paren.rstrip(" ,;:—-").strip()

        # Bound to max 450 characters at sentence boundary if necessary
        if len(result) > 450:
            match = re.search(r"^(.{1,450}[.!?])", result)
            if match:
                result = match.group(1).strip()
            else:
                result = result[:447].rstrip() + "..."

        if not result:
            if friendly_fire_dmg > 100:
                result = (
                    f"Helldiver {player_name}, High Command logs your combat service, but warns that excessive "
                    f"friendly fire ({friendly_fire_dmg:.0f} dmg) borders on treason. Watch your fire!"
                )
            elif kills >= 200 and deaths == 0:
                result = (
                    f"Flawless extraction, Helldiver {player_name}! Zero casualties and {kills:,} enemies purged—"
                    "you stand as a shining Hero of the Federation!"
                )
            else:
                result = f"Mission telemetry logged for Helldiver {player_name}. Super Earth appreciates your service to Managed Democracy."

        return result
