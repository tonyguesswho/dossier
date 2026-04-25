"""Unit tests for dossier.core.db — SQLAlchemy engine factory.

Locked by .planning/phases/02-single-pass-rag-pipeline/02-CONTEXT.md D-01, D-15,
and PATTERNS.md §"DATABASE_URL single source of truth".

These tests do NOT require a running Postgres — they verify URL plumbing and
singleton behavior only. An integration test in Plan 02-06 covers real pgvector.
Target runtime: <100ms.
"""
from __future__ import annotations

import pytest

from dossier.core import db as db_module


def test_missing_database_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings raises ValidationError when DATABASE_URL is absent.

    Replaces the legacy custom RuntimeError — pydantic's ValidationError
    surfaces a richer message but the contract ('refuse to run without a
    DB URL') is preserved. We also blank the env_file so the repo-root .env
    can't satisfy the requirement during the test run.
    """
    from dossier.core.settings import Settings
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "dossier.core.settings.Settings.model_config",
        {**Settings.model_config, "env_file": None},
    )
    db_module._reset_engine_for_tests()
    with pytest.raises(Exception, match="database_url|DATABASE_URL"):
        db_module.read_database_url()


def test_get_engine_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    # Use a sqlite in-memory URL — engine construction is cheap and driver-agnostic.
    # Settings validates DATABASE_URL must be a Postgres URL — use a fake
    # postgres-shaped URL since the engine is created lazily and we never
    # actually connect in these singleton-identity tests.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
    db_module._reset_engine_for_tests()
    e1 = db_module.get_engine()
    e2 = db_module.get_engine()
    assert e1 is e2, "get_engine() must return the same Engine instance on repeated calls"


def test_get_session_is_bound_to_shared_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings validates DATABASE_URL must be a Postgres URL — use a fake
    # postgres-shaped URL since the engine is created lazily and we never
    # actually connect in these singleton-identity tests.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
    db_module._reset_engine_for_tests()
    engine = db_module.get_engine()
    with db_module.get_session() as session:
        assert session.get_bind() is engine
