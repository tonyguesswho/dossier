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
  - Phase 3: RDS Proxy deferred to Phase 3 per CONTEXT.md §deferred — local
    Postgres doesn't need it. Phase 3 updates DATABASE_URL + adds pool_pre_ping.

Driver: psycopg v3 — matches STACK.md §2.3 and alembic/env.py.
"""
from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from dossier.observability import load_env

_engine: Engine | None = None


def read_database_url() -> str:
    """Return DATABASE_URL; raise RuntimeError on missing (same message as alembic/env.py)."""
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL not set. Copy .env.example to .env and fill in local Postgres creds."
        )
    return url


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
    global _engine
    _engine = None


__all__ = ["get_engine", "get_session", "read_database_url"]
