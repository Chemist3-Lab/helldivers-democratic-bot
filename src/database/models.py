"""SQLModel table definitions.

Defines the complete database schema for the Helldivers Democratic Bot:
- DispatchedAlert: Idempotency tracking for war/news notifications.
- HelldiverProfile: Aggregate lifetime stats per Discord user.
- MissionRecord: Individual mission extraction records.

See ARCHITECTURE.md §4 for the full schema specification and ERD.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(timezone.utc)


class DispatchedAlert(SQLModel, table=True):
    """Tracks dispatched notifications for idempotency.

    The (source, external_id) pair is unique — preventing duplicate
    broadcasts even across bot restarts.
    """

    __tablename__ = "dispatched_alerts"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_source_external"),
    )

    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(index=True)
    external_id: str = Field(index=True)
    title: str = Field(default="")
    dispatched_at: datetime = Field(default_factory=_utc_now)


class HelldiverProfile(SQLModel, table=True):
    """Aggregate lifetime stats and DVR for a Discord user."""

    __tablename__ = "helldiver_profiles"

    id: int | None = Field(default=None, primary_key=True)
    discord_id: int = Field(unique=True, index=True)
    display_name: str = Field(default="Unknown Helldiver")

    # Aggregate lifetime stats
    total_missions: int = Field(default=0)
    total_kills: int = Field(default=0)
    total_deaths: int = Field(default=0)
    total_stims_used: int = Field(default=0)
    total_friendly_fire: float = Field(default=0.0)
    accuracy_samples: int = Field(default=0)
    accuracy_sum: float = Field(default=0.0)

    # Aggregate DVR
    dvr_total: float = Field(default=0.0)
    dvr_current: float = Field(default=0.0)

    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class MissionRecord(SQLModel, table=True):
    """Individual mission extraction record from a debrief screenshot."""

    __tablename__ = "mission_records"

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="helldiver_profiles.id", index=True)
    submitted_by: int = Field(index=True)
    message_id: int = Field(unique=True)

    # Extracted stats
    kills: int = Field(default=0)
    deaths: int = Field(default=0)
    stims_used: int = Field(default=0)
    accuracy_pct: float = Field(default=0.0)
    friendly_fire_dmg: float = Field(default=0.0)
    difficulty: int = Field(default=1, ge=1, le=10)

    # Computed
    dvr_score: float = Field(default=0.0)

    # Raw payload for audit / reprocessing
    raw_gemini_response: str = Field(default="{}")

    extracted_at: datetime = Field(default_factory=_utc_now)
