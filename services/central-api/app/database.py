"""Async SQLAlchemy 2 engine, session factory and schema bootstrap.

Module 1 creates its schema on startup rather than shipping Alembic migrations:
the demo is torn down and rebuilt constantly, and there is no data to preserve
between runs. `docs/adapter-contract.md` notes where Alembic slots in for Phase 2.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for every Vigentra table."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        kwargs: dict = {"echo": False, "future": True}
        if not settings.database_url.startswith("sqlite"):
            # Modest pool: the demo is single-operator, and Postgres in Compose
            # starts with a small connection allowance.
            kwargs.update(pool_size=5, max_overflow=5, pool_pre_ping=True)
        _engine = create_async_engine(settings.database_url, **kwargs)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def init_models() -> None:
    """Create every table declared on Base if it does not already exist."""
    from . import models  # noqa: F401  - import registers the mappers

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with get_session_factory()() as session:
        yield session
