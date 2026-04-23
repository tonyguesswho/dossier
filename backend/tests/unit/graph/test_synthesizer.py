"""Unit tests for synthesizer node.

Scope:
  - synthesizer.run() is an async coroutine.
  - Brief produced by synthesize_brief is flattened into DraftClaimRef list
    with DB section names (singular 'risk' not plural 'risk_flags').
  - All 6 Brief field names map to DB section values via SECTION_FIELD_TO_DB.
  - synthesizer calls retrieve_top_k + synthesize_brief via asyncio.to_thread
    (Phase 2 sync modules bridged unchanged).
  - The async DB session is opened AFTER the to_thread bridges return
    (consistent with BLOCKER-4 / WARNING-4 rule: no DB session during LLM
    calls).
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from dossier.investigate.graph.nodes import synthesizer
from dossier.models import Brief, BriefClaim


def _make_state(investigation_id: str, **overrides):
    base = {
        "investigation_id": investigation_id,
        "company": "TestCo",
        "context_hint": None,
        "input_url": None,
        "reflection_count": 0,
        "should_regather": False,
        "targeted_sections": [],
        "founder_candidates": [],
        "retrieved_chunks": [],
        "draft_claims": [],
        "grounded_claims": [],
    }
    base.update(overrides)
    return base


def _make_brief() -> Brief:
    """Sample Brief with 1 claim per section — exercises all 6 field → DB mappings."""
    def _c(text: str) -> BriefClaim:
        return BriefClaim(claim_text=text, quoted_span=text, source_chunk_id="chunk-1")
    return Brief(
        founders=[_c("Ada founded TestCo in 2022.")],
        company=[_c("TestCo is a Series A company.")],
        market=[_c("TAM is $10B.")],
        product=[_c("Product is an AI platform.")],
        risk_flags=[_c("Regulatory risk is high.")],
        suggested_questions=[_c("What is the moat?")],
    )


class _FakeAsyncSessionCtx:
    """Async context manager stub for get_async_session() → session → session.begin()."""

    def __init__(self, executed: list):
        self._executed = executed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return self

    async def execute(self, stmt, params=None):
        self._executed.append(str(stmt))


def _install_fakes(monkeypatch, retrieved=None, brief=None):
    """Patch retrieve_top_k, synthesize_brief, and get_async_session."""
    import dossier.investigate.retrieve as retrieve_mod
    import dossier.investigate.synthesize as synthesize_mod
    import dossier.core.db as db_mod

    monkeypatch.setattr(
        retrieve_mod, "retrieve_top_k", lambda *a, **k: retrieved or []
    )
    monkeypatch.setattr(
        synthesize_mod, "synthesize_brief", lambda *a, **k: brief or _make_brief()
    )
    executed: list = []
    monkeypatch.setattr(
        db_mod, "get_async_session", lambda: _FakeAsyncSessionCtx(executed)
    )
    return executed


def test_run_is_async_coroutine():
    import inspect
    assert inspect.iscoroutinefunction(synthesizer.run)


def test_run_flattens_brief_to_draft_claims(monkeypatch):
    """All 6 Brief fields must produce DraftClaimRef entries with DB section names."""
    _install_fakes(monkeypatch)

    inv_id = str(uuid4())
    out = asyncio.run(synthesizer.run(_make_state(inv_id)))

    draft_claims = out["draft_claims"]
    assert len(draft_claims) == 6, "1 claim × 6 sections = 6 draft claims"

    sections = sorted(dc.section for dc in draft_claims)
    assert sections == [
        "company",
        "founders",
        "market",
        "product",
        "risk",  # DB-section singular; Brief.risk_flags → 'risk'
        "suggested_questions",
    ], f"sections must be DB-section values, got {sections}"


def test_run_maps_risk_flags_field_to_risk_db_section(monkeypatch):
    """Brief.risk_flags (plural field) must emit DraftClaimRef.section='risk' (singular DB)."""
    _install_fakes(monkeypatch)

    out = asyncio.run(synthesizer.run(_make_state(str(uuid4()))))
    risk_claims = [dc for dc in out["draft_claims"] if dc.section == "risk"]
    assert len(risk_claims) == 1
    assert risk_claims[0].claim_text == "Regulatory risk is high."


def test_run_preserves_quoted_span_and_source_chunk_id(monkeypatch):
    """DraftClaimRef must round-trip BriefClaim fields unmodified."""
    _install_fakes(monkeypatch)

    out = asyncio.run(synthesizer.run(_make_state(str(uuid4()))))
    for dc in out["draft_claims"]:
        # fixture uses claim_text == quoted_span
        assert dc.quoted_span == dc.claim_text
        assert dc.source_chunk_id == "chunk-1"


def test_run_with_empty_brief_returns_no_draft_claims(monkeypatch):
    """Empty Brief (6 sections all []) → draft_claims is an empty list, no crash."""
    empty_brief = Brief(
        founders=[], company=[], market=[], product=[],
        risk_flags=[], suggested_questions=[],
    )
    _install_fakes(monkeypatch, brief=empty_brief)

    out = asyncio.run(synthesizer.run(_make_state(str(uuid4()))))
    assert out == {"draft_claims": []}


def test_run_calls_update_status_synthesizing(monkeypatch):
    """UPDATE investigations.status='synthesizing' must fire via async session."""
    executed = _install_fakes(monkeypatch)
    asyncio.run(synthesizer.run(_make_state(str(uuid4()))))
    assert any("UPDATE investigations" in sql for sql in executed), (
        f"expected an UPDATE investigations statement, got {executed}"
    )
    assert any("synthesizing" in sql.lower() or "investigation_status" in sql for sql in executed)


def test_run_passes_company_to_retrieve_and_synthesize(monkeypatch):
    """Company + context_hint must flow from state into retrieve_top_k + synthesize_brief."""
    import dossier.investigate.retrieve as retrieve_mod
    import dossier.investigate.synthesize as synthesize_mod
    import dossier.core.db as db_mod

    captured = {"retrieve": None, "synth": None}

    def _fake_retrieve(investigation_id, query, *, k=6, **kw):
        captured["retrieve"] = {"investigation_id": str(investigation_id), "query": query, "k": k}
        return []

    def _fake_synth(retrieved, company, context_hint=None, **kw):
        captured["synth"] = {"company": company, "context_hint": context_hint}
        return _make_brief()

    monkeypatch.setattr(retrieve_mod, "retrieve_top_k", _fake_retrieve)
    monkeypatch.setattr(synthesize_mod, "synthesize_brief", _fake_synth)
    monkeypatch.setattr(db_mod, "get_async_session", lambda: _FakeAsyncSessionCtx([]))

    inv_id = str(uuid4())
    state = _make_state(inv_id, company="AcmeAI", context_hint="Meeting next week")
    asyncio.run(synthesizer.run(state))

    assert captured["retrieve"]["query"] == "AcmeAI"
    assert captured["retrieve"]["investigation_id"] == inv_id
    assert captured["retrieve"]["k"] == 20, "synthesizer should use top-k=20"
    assert captured["synth"]["company"] == "AcmeAI"
    assert captured["synth"]["context_hint"] == "Meeting next week"
