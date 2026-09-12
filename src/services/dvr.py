"""Democratic Valor Rating (DVR) calculation engine.

Implements the per-mission DVR formula and rolling EMA aggregation.
See ARCHITECTURE.md §5 for the complete mathematical specification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DVRWeights:
    """Tunable weights for the DVR formula."""

    kill: float = 30.0
    accuracy: float = 25.0
    support: float = 10.0
    death: float = 20.0
    friendly_fire: float = 15.0
    kill_cap: int = 500
    stim_cap: int = 20
    death_cap: int = 10
    ff_cap: float = 500.0
    ema_alpha: float = 0.3


def calculate_mission_dvr(
    kills: int,
    deaths: int,
    accuracy_pct: float,
    stims_used: int,
    friendly_fire_dmg: float,
    difficulty: int,
    w: DVRWeights = DVRWeights(),
) -> float:
    """Calculate the DVR score for a single mission extraction.

    Args:
        kills: Total enemy kills.
        deaths: Total player deaths.
        accuracy_pct: Accuracy percentage (0–100).
        stims_used: Number of stim packs used.
        friendly_fire_dmg: Cumulative friendly fire damage dealt.
        difficulty: Mission difficulty level (1–10).
        w: Tunable weight parameters.

    Returns:
        Non-negative DVR score for the mission.
    """
    diff_mult = 0.5 + 0.5 * difficulty

    kill_norm = min(kills / w.kill_cap, 1.0)
    acc_norm = accuracy_pct / 100.0
    support_score = max(1.0 - stims_used / w.stim_cap, 0.0)
    death_pen = min(deaths / w.death_cap, 1.0)
    ff_pen = min(friendly_fire_dmg / w.ff_cap, 1.0)

    raw = (
        w.kill * kill_norm
        + w.accuracy * acc_norm
        + w.support * support_score
        - w.death * death_pen
        - w.friendly_fire * ff_pen
    )
    return max(diff_mult * raw, 0.0)


def update_rolling_dvr(
    current_dvr: float,
    mission_dvr: float,
    alpha: float = 0.3,
) -> float:
    """Update the rolling DVR using exponential moving average.

    Args:
        current_dvr: Previous rolling DVR value.
        mission_dvr: DVR score from the latest mission.
        alpha: EMA smoothing factor (0 < alpha ≤ 1). Higher = more weight on recent.

    Returns:
        Updated rolling DVR.
    """
    return alpha * mission_dvr + (1 - alpha) * current_dvr
