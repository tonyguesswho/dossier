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
    monkeypatch.setattr(db_module, "load_env", lambda: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_module._reset_engine_for_tests()
    with pytest.raises(RuntimeError, match="DATABASE_URL not set"):
        db_module.read_database_url()


def test_get_engine_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_module, "load_env", lambda: None)
    # Use a sqlite in-memory URL — engine construction is cheap and driver-agnostic.
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    db_module._reset_engine_for_tests()
    e1 = db_module.get_engine()
    e2 = db_module.get_engine()
    assert e1 is e2, "get_engine() must return the same Engine instance on repeated calls"


def test_get_session_is_bound_to_shared_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_module, "load_env", lambda: None)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    db_module._reset_engine_for_tests()
    engine = db_module.get_engine()
    with db_module.get_session() as session:
        assert session.get_bind() is engine
