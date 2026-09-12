"""Backward compatibility and unified access for AI modules."""

from src.ai.vision import ScoreboardVisionExtractor, ScoreboardExtraction, PlayerExtraction
from src.ai.persona import MinistryPersona, MINISTRY_OF_TRUTH_SYSTEM_INSTRUCTION

__all__ = [
    "ScoreboardVisionExtractor",
    "ScoreboardExtraction",
    "PlayerExtraction",
    "MinistryPersona",
    "MINISTRY_OF_TRUTH_SYSTEM_INSTRUCTION",
]
