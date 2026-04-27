"""Unit tests for finalize node.

Scope:
  - finalize.run() is an async coroutine.
  - Brief is reconstructed from DraftClaimRef list (inverse of synthesizer's
    flatten step); DB-section 'risk' → Brief field 'risk_flags'.
  - ground_claims is called via asyncio.to_thread with reconstructed Brief
    and retrieved chunks (k=50, wider than synthesis k=20).
  - WARNING-3 fix: grounded_claims is populated from a SELECT on
    claims WHERE grounded_source_chunk_id IS NOT NULL.
  - status='complete' + completed_at=now() UPDATE fires.
  - Unknown section names in draft_claims are skipped (not crashed on).
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from dossier.investigate.graph.nodes import finalize
from dossier.investigate.graph.state import DraftClaimRef, GroundedClaimRef
from dossier.investigate.brief_schema import Brief


def _make_state(investigation_id: str, draft_claims=None, **overrides):
    base = {
        "investigation_id": investigation_id,
        "company": "TestCo",
        "context_hint": None,
        "input_url": None,
        "reflection_count": 0,
        "should_regather": False,
        "targeted_sections": [],
        "founder_candidates": [],
        "staged_sources": [],
        "draft_claims": draft_claims or [],
        "grounded_claims": [],
    }
    base.update(overrides)
    return base


def _make_draft_claim(section: str, text: str = "claim body") -> DraftClaimRef:
    return DraftClaimRef(
        section=section,
        claim_text=text,
        quoted_span=text,
        source_chunk_id="chunk-x",
    )


class _FakeRow:
    """Row-like object used by the fake SELECT path."""

    def __init__(self, **fields):
        for k, v in fields.items():
            setattr(self, k, v)


class _FakeAsyncSessionCtx:
    """Async session stub.

    - UPDATE statements are recorded in `executed`.
    - SELECT claims returns whatever `_select_rows` yields.
    """

    def __init__(self, executed: list, select_rows: list):
        self._executed = executed
        self._select_rows = select_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return self

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self._executed.append(sql)
        if "SELECT" in sql.upper() and "claims" in sql.lower():
            return iter(self._select_rows)
        # UPDATE paths don't need iteration
        return None


def _install_fakes(monkeypatch, retrieved=None, select_rows=None):
    """Patch retrieve_top_k, ground_claims, and get_async_session."""
    import dossier.investigate.retrieve as retrieve_mod
    import dossier.investigate.ground as ground_mod
    import dossier.core.db as db_mod

    captured = {"ground": None, "retrieve_k": None}

    def _fake_retrieve(investigation_id, query, *, k=6, **kw):
        captured["retrieve_k"] = k
        return retrieved or []

    def _fake_ground(investigation_id, brief, retrieved_arg, **kw):
        captured["ground"] = {
            "investigation_id": str(investigation_id),
            "brief": brief,
            "retrieved": retrieved_arg,
        }

    monkeypatch.setattr(retrieve_mod, "retrieve_top_k", _fake_retrieve)
    monkeypatch.setattr(ground_mod, "ground_claims", _fake_ground)

    executed: list = []
    monkeypatch.setattr(
        db_mod,
        "get_async_session",
        lambda: _FakeAsyncSessionCtx(executed, select_rows or []),
    )
    return captured, executed


def test_run_reconstructs_brief_from_draft_claims(monkeypatch):
    """Brief passed to ground_claims must have draft_claims grouped by db→field."""
    captured, _ = _install_fakes(monkeypatch)

    draft_claims = [
        _make_draft_claim("founders", "Ada founded it"),
        _make_draft_claim("company", "Company A"),
        _make_draft_claim("risk", "Risk R"),  # singular DB → plural field
        _make_draft_claim("suggested_questions", "Q1"),
    ]
    asyncio.run(finalize.run(_make_state(str(uuid4()), draft_claims=draft_claims)))

    brief: Brief = captured["ground"]["brief"]
    assert len(brief.founders) == 1
    assert brief.founders[0].claim_text == "Ada founded it"
    assert len(brief.company) == 1
    assert len(brief.risk_flags) == 1, "DB section 'risk' → Brief field 'risk_flags'"
    assert brief.risk_flags[0].claim_text == "Risk R"
    assert len(brief.suggested_questions) == 1
    # Sections with no draft claims become empty lists (Brief has no defaults)
    assert brief.market == []
    assert brief.product == []


def test_run_populates_grounded_claims_from_db_select(monkeypatch):
    """WARNING-3 fix: grounded_claims must come from DB SELECT on non-null chunk ids."""
    rows = [
        _FakeRow(
            id=uuid4(),
            section="founders",
            claim_text="Ada founded TestCo",
            grounded_source_chunk_id=uuid4(),
            grounded_span_start=10,
            grounded_span_end=30,
        ),
        _FakeRow(
            id=uuid4(),
            section="market",
            claim_text="TAM is $10B",
            grounded_source_chunk_id=uuid4(),
            grounded_span_start=5,
            grounded_span_end=18,
        ),
    ]
    _install_fakes(monkeypatch, select_rows=rows)

    out = asyncio.run(finalize.run(_make_state(str(uuid4()))))
    grounded = out["grounded_claims"]
    assert len(grounded) == 2
    assert all(isinstance(g, GroundedClaimRef) for g in grounded)
    sections = sorted(g.section for g in grounded)
    assert sections == ["founders", "market"]
    # Offsets + chunk_id must round-trip as strings
    for g in grounded:
        assert g.grounded_source_chunk_id is not None
        assert isinstance(g.grounded_source_chunk_id, str)


def test_run_returns_empty_grounded_claims_when_select_empty(monkeypatch):
    """No rows with grounded_source_chunk_id IS NOT NULL → grounded_claims=[]."""
    _install_fakes(monkeypatch, select_rows=[])
    out = asyncio.run(finalize.run(_make_state(str(uuid4()))))
    assert out == {"grounded_claims": []}


def test_run_fires_update_status_complete(monkeypatch):
    """UPDATE investigations SET status='complete', completed_at=now() must fire."""
    _, executed = _install_fakes(monkeypatch)
    asyncio.run(finalize.run(_make_state(str(uuid4()))))
    assert any("UPDATE investigations" in sql for sql in executed)
    assert any("completed_at" in sql for sql in executed)


def test_run_retrieves_with_k_50(monkeypatch):
    """Grounding must use a wider retrieval (k=50) than synthesis (k=20)."""
    captured, _ = _install_fakes(monkeypatch)
    asyncio.run(finalize.run(_make_state(str(uuid4()))))
    assert captured["retrieve_k"] == 50


def test_run_skips_unknown_section(monkeypatch):
    """Unknown draft_claim.section values must not crash — logged + skipped."""
    captured, _ = _install_fakes(monkeypatch)

    draft_claims = [
        _make_draft_claim("founders"),
        _make_draft_claim("not_a_section"),  # should be skipped silently
    ]
    out = asyncio.run(finalize.run(_make_state(str(uuid4()), draft_claims=draft_claims)))

    brief: Brief = captured["ground"]["brief"]
    # Only the valid section made it in
    assert len(brief.founders) == 1
    total_claims = sum(
        len(getattr(brief, f))
        for f in ("founders", "company", "market", "product", "risk_flags", "suggested_questions")
    )
    assert total_claims == 1
    # grounded_claims still returns [] from the (empty) SELECT path
    assert out["grounded_claims"] == []


def test_run_with_empty_draft_claims_calls_ground_with_empty_brief(monkeypatch):
    """draft_claims=[] → ground_claims still called with a well-formed empty Brief."""
    captured, executed = _install_fakes(monkeypatch)

    asyncio.run(finalize.run(_make_state(str(uuid4()), draft_claims=[])))

    brief: Brief = captured["ground"]["brief"]
    # Every section is an empty list (Brief has no defaults, so constructor must explicitly set them)
    for f in ("founders", "company", "market", "product", "risk_flags", "suggested_questions"):
        assert getattr(brief, f) == []
    # status=complete UPDATE still fires
    assert any("UPDATE investigations" in sql for sql in executed)
