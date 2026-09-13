"""Application configuration via environment variables.

Uses pydantic-settings to load and validate all configuration from
environment variables and/or a `.env` file in the project root.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve project root (two levels up from this file: src/config.py → project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_database_url() -> str:
    """Determine the default database URL based on runtime environment.

    - If running inside Docker / Railway container with /app/data volume:
      defaults to 'sqlite+aiosqlite:////app/data/democracy.db'
    - Otherwise defaults to local development:
      'sqlite+aiosqlite:///democracy.db'
    """
    if Path("/app/data").is_dir() or os.path.exists("/.dockerenv") or os.environ.get("RAILWAY_ENVIRONMENT"):
        return "sqlite+aiosqlite:////app/data/democracy.db"
    return "sqlite+aiosqlite:///democracy.db"


class Settings(BaseSettings):
    """Typed, validated configuration for the Helldivers Democratic Bot."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Required Secrets ────────────────────────────────────────
    discord_token: str = Field(
        ...,
        description="Discord bot token from the Developer Portal.",
    )
    gemini_api_key: str = Field(
        ...,
        description="Google AI Studio API key for Gemini.",
    )

    # ── Required Discord IDs ────────────────────────────────────
    guild_id: int | None = Field(
        default=None,
        description="Optional target Discord server snowflake for instant dev sync. If omitted or 0, commands sync globally.",
    )

    @field_validator("guild_id", mode="before")
    @classmethod
    def parse_guild_id(cls, v: object) -> int | None:
        if v is None or v == "" or v == 0 or v == "0":
            return None
        try:
            val = int(v)  # type: ignore[call-overload]
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None
    helldiver_channel_id: int | None = Field(
        default=None,
        description="Unified Discord channel ID for all war alerts, news dispatches, and mission debriefs.",
    )
    alert_channel_id_legacy: int | None = Field(
        default=None,
        alias="alert_channel_id",
        description="Legacy alias for alert channel.",
    )
    debrief_channel_id_legacy: int | None = Field(
        default=None,
        alias="debrief_channel_id",
        description="Legacy alias for debrief channel.",
    )
    helldiver_role_id: int = Field(..., description="Role ID to ping on alerts.")

    @model_validator(mode="after")
    def _resolve_channel_ids(self) -> Settings:
        if self.helldiver_channel_id is None:
            resolved = self.alert_channel_id_legacy or self.debrief_channel_id_legacy
            if resolved is None:
                raise ValueError(
                    "Missing required setting: 'helldiver_channel_id' (or legacy 'alert_channel_id')"
                )
            self.helldiver_channel_id = resolved
        return self

    @property
    def alert_channel_id(self) -> int:
        """Backward compatibility alias for helldiver_channel_id."""
        assert self.helldiver_channel_id is not None
        return self.helldiver_channel_id

    @property
    def debrief_channel_id(self) -> int:
        """Backward compatibility alias for helldiver_channel_id."""
        assert self.helldiver_channel_id is not None
        return self.helldiver_channel_id

    # ── Database ────────────────────────────────────────────────
    database_url: str = Field(
        default_factory=_default_database_url,
        description="Async SQLAlchemy database URL (e.g., sqlite+aiosqlite:///democracy.db).",
    )

    # ── Optional ────────────────────────────────────────────────
    war_poll_seconds: int = Field(
        default=300,
        ge=60,
        description="Interval in seconds between Helldivers war API polls.",
    )
    news_poll_seconds: int = Field(
        default=600,
        ge=60,
        description="Interval in seconds between Steam News API polls.",
    )
    hd2_contact: str = Field(
        default="https://github.com/Chemist3-Lab/helldivers-democratic-bot",
        description="Contact information sent via X-Super-Contact header to api.helldivers2.dev.",
    )
    log_level: str = Field(
        default="INFO",
        description="Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    gemini_model: str = Field(
        default="gemini-3.6-flash",
        description="Gemini model name for chat and vision tasks.",
    )
    gemini_embedding_model: str = Field(
        default="text-embedding-004",
        description="Gemini model name for text embeddings.",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            msg = f"log_level must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return upper

    @property
    def db_url(self) -> str:
        """Return database_url for backwards compatibility with engine initializers."""
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached, validated application settings singleton."""
    return Settings()  # type: ignore[call-arg]
