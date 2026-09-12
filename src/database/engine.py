"""Async SQLite engine with WAL mode.

Creates and manages the async SQLAlchemy engine using aiosqlite.
Provides database initialization (table creation) and the session factory.

See ARCHITECTURE.md §4 for the complete schema specification and
§6.1 for async safety requirements.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

log = logging.getLogger(__name__)

# Module-level engine and session factory — initialized by init_db()
_engine = None
_async_session_factory = None


async def init_db(db_url: str) -> None:
    """Initialize the async database engine and create all tables.

    Args:
        db_url: SQLAlchemy async database URL
                (e.g. "sqlite+aiosqlite:///data/helldivers.db").
    """
    global _engine, _async_session_factory  # noqa: PLW0603

    # If sqlite file path, ensure parent directory exists
    if "sqlite" in db_url and ":///" in db_url:
        path_str = db_url.split(":///", 1)[1]
        if path_str and not path_str.startswith(":memory:"):
            from pathlib import Path
            Path(path_str).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_async_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Enable WAL mode for concurrent read/write
    async with _engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    # Import models to register them with SQLModel metadata
    from src.database import models  # noqa: F401

    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    _async_session_factory = sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    log.info("Database engine initialized with WAL mode")


def get_session() -> AsyncSession:
    """Return a new async session from the factory.

    Raises:
        RuntimeError: If init_db() has not been called yet.
    """
    if _async_session_factory is None:
        msg = "Database not initialized. Call init_db() first."
        raise RuntimeError(msg)
    return _async_session_factory()  # type: ignore[return-value]
