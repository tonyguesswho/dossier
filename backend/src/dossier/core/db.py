"""SQLAlchemy engine and session factory for Dossier.

Single source of truth for runtime DB connections. `backend/alembic/env.py` is
the migration-time equivalent; both read the same DATABASE_URL from the repo-root
.env file so local pgvector, test DB, and Phase 3 RDS all route through the same
connection string.

DATABASE_URL is hardcoded in exactly 5 places (contract per PATTERNS.md §"DATABASE_URL
single source of truth"):
  1. .env.example (default value for copy-to-.env)
  2. docker-compose.yml (postgres credentials)
  3. alembic.ini (via env.py injection)
  4. backend/alembic/env.py (migration-time)
  5. backend/src/dossier/core/db.py (runtime — this file)

Why a dedicated module:
  1. **Env-before-engine safety.** load_env() runs before SQLAlchemy reads os.environ.
  2. **One engine per process.** A module-level cache prevents N-engine leaks in
     FastAPI handlers that would otherwise exhaust Postgres connections.
  3. **Phase 3 swap-point.** RDS Proxy + IAM auth (ARCHITECTURE.md §10) ships via
     the same get_engine() surface — only the connection URL changes.

Rejected alternatives:
  - Pass Engine through FastAPI Depends() everywhere: valid but verbose; module
    singleton is simpler for a single-Lambda-process local dev setup.
  - create_engine at import time: triggers load_env during test collection, can
    break tests that monkeypatch DATABASE_URL.

Phase coverage:
  - Phase 2: local docker-compose pgvector:pg17 per CONTEXT.md D-01.
  - Phase 3 (this plan): adds async engine + get_async_session() for LangGraph
    nodes. The sync engine stays — api-lambda's FastAPI routes are sync.
    RDS Proxy endpoint is wired via DATABASE_URL env var; prepare_threshold=0
    in connect_args prevents RDS Proxy connection pinning (Pitfall 7.4).

Driver: psycopg v3 — matches STACK.md §2.3 and alembic/env.py.
"""
from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from dossier.core.settings import get_settings

_engine: Engine | None = None


def read_database_url() -> str:
    """Return DATABASE_URL; thin pass-through to Settings.

    Kept as a function (rather than callers reading Settings directly) so the
    dependency injection seam stays at one place — `_reset_engine_for_tests()`
    + monkeypatching `os.environ["DATABASE_URL"]` continues to work.
    """
    return get_settings().database_url


def get_engine() -> Engine:
    """Return the process-wide cached SQLAlchemy Engine (singleton)."""
    global _engine
    if _engine is None:
        # future=True matches backend/src/dossier/eval/seed.py line 88 exactly.
        _engine = create_engine(read_database_url(), future=True)
    return _engine


def get_session() -> Session:
    """Return a fresh Session bound to the shared Engine. Use as a context manager."""
    return Session(bind=get_engine(), future=True)


def _reset_engine_for_tests() -> None:
    """Testing-only hook: forget the cached engine so monkeypatched DATABASE_URL takes effect."""
    global _engine, _async_engine
    _engine = None
    _async_engine = None


# --- Async engine for LangGraph graph nodes (Phase 3) ------------------------
# Separate from the sync engine above; graph nodes are async, FastAPI routes
# are sync. Keeping both lets api-lambda (sync) and investigate-lambda (async)
# each use the engine flavor that matches their runtime model without collision.
#
# Why a second engine at all:
#   SQLAlchemy's AsyncEngine cannot drive a sync Session, and an Engine cannot
#   drive AsyncSession. Trying to share would either block the event loop
#   (sync-in-async) or crash (async-in-sync). Two engines, same DATABASE_URL.
#
# Why prepare_threshold=0:
#   psycopg3 prepares statements after 5 reuses by default (prepare_threshold=5).
#   Each prepared statement leaves session state on the Postgres connection.
#   RDS Proxy detects session-state and PINS the connection to that client,
#   defeating the purpose of RDS Proxy entirely and exhausting the t3.micro's
#   ~85-connection cap under Lambda concurrency (Pitfall 7.4). Setting
#   prepare_threshold=0 disables prepared statements — the connection stays
#   clean and RDS Proxy keeps multiplexing.
#
# Why pool_size=2 / max_overflow=0:
#   investigate-lambda is single-invocation; one graph run at a time. Two DB
#   connections is enough headroom for ingest_and_embed + finalize concurrent
#   writes. max_overflow=0 prevents runaway pool growth if a node leaks a
#   session (fail loud, not silent connection-cap exhaustion).

_async_engine: AsyncEngine | None = None


def _make_async_database_url(url: str) -> str:
    """Convert a sync-style Postgres URL to the psycopg3 async-compatible form.

    SQLAlchemy's psycopg v3 dialect is `postgresql+psycopg`; it autodetects
    async vs sync based on which engine factory (create_engine vs
    create_async_engine) you call. The `postgresql://` and
    `postgresql+psycopg2://` schemes both need normalizing to `postgresql+psycopg://`
    so create_async_engine picks the psycopg3 driver.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    return url  # already psycopg-compatible (e.g. postgresql+psycopg://...)


def get_async_engine() -> AsyncEngine:
    """Return the process-wide cached SQLAlchemy AsyncEngine (singleton).

    Graph nodes use this for DB writes (sources, source_chunks, claims).
    `prepare_threshold=0` in connect_args prevents RDS Proxy pinning
    (Pitfall 7.4) — do not remove.
    """
    global _async_engine
    if _async_engine is None:
        url = _make_async_database_url(read_database_url())
        _async_engine = create_async_engine(
            url,
            pool_size=2,          # investigate-lambda is single-invocation
            max_overflow=0,       # fail loud on pool exhaustion, not silently grow
            pool_pre_ping=True,   # detect stale connections (Lambda warm-start after idle)
            future=True,
            connect_args={
                # CRITICAL: prevents RDS Proxy connection pinning (Pitfall 7.4).
                # psycopg3-specific kwarg; disables prepared statements.
                "prepare_threshold": 0,
            },
        )
    return _async_engine


def get_async_session() -> AsyncSession:
    """Return a fresh AsyncSession bound to the shared AsyncEngine.

    Usage in graph nodes:

        async with get_async_session() as session:
            async with session.begin():
                await session.execute(...)

    The sessionmaker is created per call (cheap) so a future test hook can
    reset the engine (_reset_engine_for_tests) without stale session factory
    references surviving the reset.
    """
    engine = get_async_engine()
    async_session_factory = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,  # graph nodes read attributes after commit
    )
    return async_session_factory()


__all__ = [
    "get_async_engine",
    "get_async_session",
    "get_engine",
    "get_session",
    "read_database_url",
]
