"""Unit tests for ingest_and_embed node.

Scope:
  - run() with no staged sources for the current pass is a no-op.
  - staged sources flow into ingest_tool_results when the classifier passes.
  - only the current reflection pass is ingested.
  - run() bypasses the DB session when there are no injection chunks.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from dossier.investigate.graph.nodes import ingest_and_embed
from dossier.investigate.graph.state import StagedSourceRef


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
        "staged_sources": [],
        "draft_claims": [],
        "grounded_claims": [],
    }
    base.update(overrides)
    return base


def _make_staged_source(
    url: str,
    text: str = "Some source content about TestCo.",
    *,
    pass_index: int = 0,
) -> StagedSourceRef:
    return StagedSourceRef(
        url=url,
        source_kind="web",
        text=text,
        title="TestCo Home",
        fetched_at=datetime.now(timezone.utc),
        raw_metadata={},
        section_hint="general",
        pass_index=pass_index,
    )


async def _passthrough_classifier(_chunk_text: str) -> tuple[str, str]:
    return "clean", ""


def test_run_with_no_staged_sources_is_noop():
    inv_id = str(uuid4())
    state = _make_state(inv_id)
    result = asyncio.run(ingest_and_embed.run(state))
    assert result == {}


def test_run_passes_current_pass_sources_to_ingest_tool_results(monkeypatch):
    inv_id = str(uuid4())
    src1 = _make_staged_source("https://example.com/a", text="aaa " * 50)
    src2 = _make_staged_source("https://example.com/b", text="bbb " * 50)

    captured: dict = {}

    def _fake_ingest(investigation_id, results, **kwargs):
        captured["investigation_id"] = str(investigation_id)
        captured["urls"] = sorted(r.url for r in results)

    import dossier.investigate.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "ingest_tool_results", _fake_ingest)
    monkeypatch.setattr(ingest_and_embed, "_classify_chunk", _passthrough_classifier)

    state = _make_state(inv_id, staged_sources=[src1, src2])
    out = asyncio.run(ingest_and_embed.run(state))

    assert out == {}
    assert captured["investigation_id"] == inv_id
    assert captured["urls"] == ["https://example.com/a", "https://example.com/b"]


def test_run_only_uses_current_reflection_pass(monkeypatch):
    inv_id = str(uuid4())
    current = _make_staged_source("https://current.com", text="ccc " * 50, pass_index=1)
    previous = _make_staged_source("https://previous.com", text="ppp " * 50, pass_index=0)
    captured: dict = {}

    def _fake_ingest(_investigation_id, results, **kwargs):
        captured["urls"] = [r.url for r in results]

    import dossier.investigate.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "ingest_tool_results", _fake_ingest)
    monkeypatch.setattr(ingest_and_embed, "_classify_chunk", _passthrough_classifier)

    asyncio.run(
        ingest_and_embed.run(
            _make_state(inv_id, reflection_count=1, staged_sources=[previous, current])
        )
    )
    assert captured["urls"] == ["https://current.com"]


def test_run_skips_ingest_when_only_empty_text_sources(monkeypatch):
    inv_id = str(uuid4())
    source = _make_staged_source("https://x.com", text="")

    called = []
    import dossier.investigate.ingest as ingest_mod
    monkeypatch.setattr(
        ingest_mod, "ingest_tool_results", lambda *a, **k: called.append("yes")
    )

    out = asyncio.run(ingest_and_embed.run(_make_state(inv_id, staged_sources=[source])))
    assert out == {}
    assert called == []
