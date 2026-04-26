"""SQLAlchemy engine factories — sync for FastAPI routes, async for the graph.

Both engines read the same DATABASE_URL from Settings. The async engine
sets `prepare_threshold=0` on the psycopg3 connection to keep RDS Proxy
from pinning connections (which would defeat its multiplexing).
"""
from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from dossier.core.settings import get_settings

_engine: Engine | None = None
_async_engine: AsyncEngine | None = None


def read_database_url() -> str:
    return get_settings().database_url


def get_engine() -> Engine:
    """Process-wide sync Engine, lazy-built and cached."""
    global _engine
    if _engine is None:
        _engine = create_engine(read_database_url(), future=True)
    return _engine


def get_session() -> Session:
    """Fresh Session bound to the shared Engine — use as a context manager."""
    return Session(bind=get_engine(), future=True)


def _reset_engine_for_tests() -> None:
    """Drop the cached engines so the next call picks up a new DATABASE_URL."""
    global _engine, _async_engine
    _engine = None
    _async_engine = None


def _make_async_database_url(url: str) -> str:
    """Normalize the URL scheme to `postgresql+psycopg://` for create_async_engine."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    return url


def get_async_engine() -> AsyncEngine:
    """Process-wide async Engine for the graph nodes."""
    global _async_engine
    if _async_engine is None:
        url = _make_async_database_url(read_database_url())
        _async_engine = create_async_engine(
            url,
            pool_size=2,
            max_overflow=0,
            pool_pre_ping=True,
            future=True,
            # prepare_threshold=0 disables psycopg3 prepared statements so
            # RDS Proxy can multiplex the connection cleanly.
            connect_args={"prepare_threshold": 0},
        )
    return _async_engine


def get_async_session() -> AsyncSession:
    """Fresh AsyncSession bound to the shared AsyncEngine."""
    engine = get_async_engine()
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return factory()


__all__ = [
    "get_async_engine",
    "get_async_session",
    "get_engine",
    "get_session",
    "read_database_url",
]
