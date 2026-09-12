"""Repository pattern — typed async CRUD operations for database models.

Provides async database access methods organized by domain concern:
- AlertRepository: Idempotent dispatch tracking.
- ProfileRepository: Helldiver profile upserts and leaderboard queries.
- MissionRepository: Mission record inserts and history queries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from src.database.engine import get_session
from src.database.models import DispatchedAlert, HelldiverProfile, MissionRecord
from src.services.dvr import update_rolling_dvr

log = logging.getLogger(__name__)


class AlertRepository:
    """Repository for tracking dispatched alerts (idempotency)."""

    @staticmethod
    async def is_dispatched(source: str, external_id: str) -> bool:
        """Check if an alert with source and external_id has already been sent."""
        async with get_session() as session:
            stmt = select(DispatchedAlert).where(
                DispatchedAlert.source == source,
                DispatchedAlert.external_id == external_id,
            )
            result = await session.execute(stmt)
            return result.scalars().first() is not None

    @staticmethod
    async def get_dispatched_ids(source: str) -> set[str]:
        """Fetch all external_ids that have been dispatched for a given source."""
        async with get_session() as session:
            stmt = select(DispatchedAlert.external_id).where(DispatchedAlert.source == source)
            result = await session.execute(stmt)
            return set(result.scalars().all())

    @staticmethod
    async def mark_dispatched(source: str, external_id: str, title: str = "") -> bool:
        """Mark an alert as dispatched. Returns True if inserted, False if already exists."""
        async with get_session() as session:
            stmt = select(DispatchedAlert).where(
                DispatchedAlert.source == source,
                DispatchedAlert.external_id == external_id,
            )
            existing = (await session.execute(stmt)).scalars().first()
            if existing is not None:
                return False

            alert = DispatchedAlert(
                source=source,
                external_id=external_id,
                title=title[:255],
                dispatched_at=datetime.now(timezone.utc),
            )
            session.add(alert)
            await session.commit()
            return True


class ProfileRepository:
    """Repository for Helldiver player profiles and statistics."""

    @staticmethod
    async def get_by_discord_id(discord_id: int) -> HelldiverProfile | None:
        """Get profile by Discord user ID."""
        async with get_session() as session:
            stmt = select(HelldiverProfile).where(HelldiverProfile.discord_id == discord_id)
            result = await session.execute(stmt)
            return result.scalars().first()

    @staticmethod
    async def get_or_create_profile(discord_id: int, display_name: str) -> HelldiverProfile:
        """Get or create a profile for a given Discord user."""
        async with get_session() as session:
            stmt = select(HelldiverProfile).where(HelldiverProfile.discord_id == discord_id)
            profile = (await session.execute(stmt)).scalars().first()
            if profile is None:
                profile = HelldiverProfile(
                    discord_id=discord_id,
                    display_name=display_name,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                session.add(profile)
                await session.commit()
                await session.refresh(profile)
            return profile

    @staticmethod
    async def record_mission_stats(
        discord_id: int,
        display_name: str,
        kills: int,
        deaths: int,
        stims_used: int,
        accuracy_pct: float,
        friendly_fire_dmg: float,
        dvr_score: float,
    ) -> HelldiverProfile:
        """Update aggregate stats and rolling DVR after a debrief extraction."""
        async with get_session() as session:
            stmt = select(HelldiverProfile).where(HelldiverProfile.discord_id == discord_id)
            profile = (await session.execute(stmt)).scalars().first()
            if profile is None:
                profile = HelldiverProfile(
                    discord_id=discord_id,
                    display_name=display_name,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(profile)

            profile.display_name = display_name
            profile.total_missions += 1
            profile.total_kills += kills
            profile.total_deaths += deaths
            profile.total_stims_used += stims_used
            profile.total_friendly_fire += friendly_fire_dmg
            profile.accuracy_samples += 1
            profile.accuracy_sum += accuracy_pct
            profile.dvr_total += dvr_score

            # Update rolling DVR with EMA
            profile.dvr_current = update_rolling_dvr(profile.dvr_current, dvr_score)
            profile.updated_at = datetime.now(timezone.utc)

            await session.commit()
            await session.refresh(profile)
            return profile

    @staticmethod
    async def get_leaderboard(limit: int = 10, by_accuracy: bool = False) -> list[HelldiverProfile]:
        """Fetch top Helldivers ordered by current DVR or average accuracy."""
        async with get_session() as session:
            if by_accuracy:
                stmt = (
                    select(HelldiverProfile)
                    .where(HelldiverProfile.accuracy_samples > 0)
                    .order_by(desc(col(HelldiverProfile.accuracy_sum) / col(HelldiverProfile.accuracy_samples)))
                    .limit(limit)
                )
            else:
                stmt = (
                    select(HelldiverProfile)
                    .order_by(desc(HelldiverProfile.dvr_current))
                    .limit(limit)
                )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    @staticmethod
    async def purge_all_records() -> int:
        """Purge all Helldiver dossiers and combat debrief records.

        Deletes MissionRecord entries first to satisfy foreign key constraints,
        then deletes HelldiverProfile entries.
        """
        async with get_session() as session:
            await session.execute(delete(MissionRecord))
            res = await session.execute(delete(HelldiverProfile))
            await session.commit()
            return res.rowcount if hasattr(res, "rowcount") else 0


class MissionRepository:
    """Repository for individual mission record extractions."""

    @staticmethod
    async def is_message_processed(message_id: int) -> bool:
        """Check if a Discord message has already been processed to prevent duplicates."""
        async with get_session() as session:
            stmt = select(MissionRecord).where(MissionRecord.message_id == message_id)
            result = await session.execute(stmt)
            return result.scalars().first() is not None

    @staticmethod
    async def create_mission_record(
        profile_id: int,
        submitted_by: int,
        message_id: int,
        kills: int,
        deaths: int,
        stims_used: int,
        accuracy_pct: float,
        friendly_fire_dmg: float,
        difficulty: int,
        dvr_score: float,
        raw_gemini_response: str = "{}",
    ) -> MissionRecord:
        """Create a new mission extraction record in the database."""
        async with get_session() as session:
            record = MissionRecord(
                profile_id=profile_id,
                submitted_by=submitted_by,
                message_id=message_id,
                kills=kills,
                deaths=deaths,
                stims_used=stims_used,
                accuracy_pct=accuracy_pct,
                friendly_fire_dmg=friendly_fire_dmg,
                difficulty=difficulty,
                dvr_score=dvr_score,
                raw_gemini_response=raw_gemini_response,
                extracted_at=datetime.now(timezone.utc),
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    @staticmethod
    async def get_recent_missions_for_profile(profile_id: int, limit: int = 5) -> list[MissionRecord]:
        """Fetch recent mission records for a given Helldiver profile."""
        async with get_session() as session:
            stmt = (
                select(MissionRecord)
                .where(MissionRecord.profile_id == profile_id)
                .order_by(desc(MissionRecord.extracted_at))
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())
