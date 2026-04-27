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
    global _engine
    if _engine is None:
        _engine = create_engine(read_database_url(), future=True)
    return _engine


def get_session() -> Session:
    return Session(bind=get_engine(), future=True)


def _reset_engine_for_tests() -> None:
    global _engine, _async_engine
    _engine = None
    _async_engine = None


def _make_async_database_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    return url


def get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        url = _make_async_database_url(read_database_url())
        _async_engine = create_async_engine(
            url,
            pool_size=2,
            max_overflow=0,
            pool_pre_ping=True,
            future=True,
            # prepare_threshold=0 disables psycopg3 prepared statements so RDS Proxy can multiplex.
            connect_args={"prepare_threshold": 0},
        )
    return _async_engine


def get_async_session() -> AsyncSession:
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
