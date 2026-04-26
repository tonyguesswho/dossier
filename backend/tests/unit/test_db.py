"""Tests for the SQLAlchemy engine factory (no real Postgres)."""
from __future__ import annotations

import pytest

from dossier.core import db as db_module


def test_get_engine_returns_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
    db_module._reset_engine_for_tests()
    e1 = db_module.get_engine()
    e2 = db_module.get_engine()
    assert e1 is e2
