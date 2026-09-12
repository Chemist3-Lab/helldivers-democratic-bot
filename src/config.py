"""Application configuration via environment variables.

Uses pydantic-settings to load and validate all configuration from
environment variables and/or a `.env` file in the project root.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve project root (two levels up from this file: src/config.py → project root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    guild_id: int = Field(..., description="Target Discord server snowflake.")
    alert_channel_id: int = Field(..., description="Channel ID for war/news alerts.")
    debrief_channel_id: int = Field(..., description="Channel ID for scoreboard screenshots.")
    helldiver_role_id: int = Field(..., description="Role ID to ping on alerts.")

    # ── Optional ────────────────────────────────────────────────
    db_path: str = Field(
        default="data/helldivers.db",
        description="Path to the SQLite database file (relative to project root).",
    )
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
    log_level: str = Field(
        default="INFO",
        description="Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
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
    def resolved_db_path(self) -> Path:
        """Return the absolute path to the SQLite database."""
        p = Path(self.db_path)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_url(self) -> str:
        """Return the async SQLAlchemy database URL."""
        return f"sqlite+aiosqlite:///{self.resolved_db_path}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached, validated application settings singleton."""
    return Settings()  # type: ignore[call-arg]
