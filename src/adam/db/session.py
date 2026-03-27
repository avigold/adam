"""Database engine and session factory.

Supports SQLite (default, zero-config) and Postgres (optional override).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from adam.config import Settings
from adam.models.base import Base


def get_engine(
    settings: Settings | None = None,
    project_dir: str = ".",
) -> AsyncEngine:
    """Create a database engine. Caller manages lifecycle (dispose)."""
    s = settings or Settings()
    url = s.db.get_url(project_dir)

    is_sqlite = url.startswith("sqlite")

    if is_sqlite:
        # Ensure .adam directory exists
        db_path = url.split("///")[-1] if "///" in url else ""
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        return create_async_engine(
            url,
            echo=s.db.echo,
            connect_args={"check_same_thread": False},
        )

    # Postgres or other
    return create_async_engine(
        url,
        echo=s.db.echo,
        pool_size=5,
        max_overflow=10,
    )


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables. Used for SQLite (no Alembic needed)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_session(
    engine: AsyncEngine | None = None,
    settings: Settings | None = None,
    project_dir: str = ".",
) -> AsyncGenerator[AsyncSession, None]:
    eng = engine or get_engine(settings, project_dir)
    factory = get_session_factory(eng)
    async with factory() as session:
        yield session
