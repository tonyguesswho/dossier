"""Unit tests for pipeline.run_investigation — orchestration logic only.

These tests cover the Phase 2 orchestrator contract (CONTEXT.md D-04/D-05/D-14/D-16):
  - `_resolve_inputs` handles name/url + optional HINT_SEPARATOR encoding; rejects 'deck'.
  - `_brief_to_markdown` renders 6 sections deterministically.
  - `run_investigation` progresses status through gathering → synthesizing → grounding → complete.
  - On synthesize failure, status=failed and error is captured (D-05 fail-fast).
  - Firecrawl budget (`_BUDGET` dict keyed by investigation_id) is popped at end-of-run.
  - Missing investigation row writes status=failed with 'not found' error and does not raise.

All external dependencies (tools, ingest, retrieve, synthesize, ground, Langfuse, DB)
are monkeypatched. Real DB + real pgvector live in test_pipeline_smoke.py (integration).

Target runtime: <500ms for this file.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from dossier.core.exceptions import PipelineError
from dossier.investigate import pipeline as pipeline_mod
from dossier.investigate.pipeline import (
    HINT_SEPARATOR,
    _brief_to_markdown,
    _resolve_inputs,
    run_investigation,
)
from dossier.investigate.tools import firecrawl as firecrawl_tool
from dossier.models import Brief, BriefClaim


# ---------------------------------------------------------------------------
# Fake engine — captures UPDATE params in a list, returns a canned row on SELECT.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row) -> None:
        self.row = row
        self.updates: list[dict] = []
        self.selects: list[dict] = []

    def execute(self, sql, params):
        sql_str = str(sql).strip().lower()
        if sql_str.startswith("select"):
            self.selects.append(dict(params))
            return _FakeResult(self.row)
        # UPDATE
        self.updates.append(dict(params))
        return _FakeResult(None)


class _FakeEngine:
    def __init__(self, row) -> None:
        self.conn = _FakeConn(row)

    def _ctx(self):
        engine = self

        class _Ctx:
            def __enter__(self_inner):
                return engine.conn

            def __exit__(self_inner, *a):
                return False

        return _Ctx()

    def connect(self):
        return self._ctx()

    def begin(self):
        return self._ctx()


# ---------------------------------------------------------------------------
# Fake Langfuse — context-manager span that swallows update() calls.
# ---------------------------------------------------------------------------


class _FakeSpan:
    def update(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeLangfuse:
    def start_as_current_observation(self, **kwargs):
        return _FakeSpan()

    def get_trace_id(self):
        return "fake-trace-1"

    def flush(self):
        pass

    def shutdown(self):
        pass


@pytest.fixture
def patched_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Patch every external dep so run_investigation is purely orchestration."""
    monkeypatch.setattr(
        pipeline_mod, "get_langfuse_client", lambda strict=False: _FakeLangfuse()
    )
    monkeypatch.setattr(
        pipeline_mod, "flush_and_shutdown", lambda client, lambda_sleep=False: None
    )
    # pipeline imports `from langfuse import propagate_attributes` inside run_investigation.
    # Stub the module in sys.modules so the import resolves to our no-op.
    import sys
    import types

    fake_lf = types.ModuleType("langfuse")

    class _PA:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake_lf.propagate_attributes = _PA  # type: ignore[attr-defined]
    # Preserve anything already cached (e.g., langfuse.get_client used by observability).
    original = sys.modules.get("langfuse")
    monkeypatch.setitem(sys.modules, "langfuse", fake_lf)
    yield monkeypatch
    if original is not None:
        sys.modules["langfuse"] = original


# ---------------------------------------------------------------------------
# _resolve_inputs
# ---------------------------------------------------------------------------


def test_resolve_inputs_name_without_hint() -> None:
    assert _resolve_inputs("name", "Acme AI") == ("Acme AI", None, None)


def test_resolve_inputs_name_with_hint() -> None:
    raw = f"Acme AI{HINT_SEPARATOR}Series A, AI infra"
    assert _resolve_inputs("name", raw) == ("Acme AI", None, "Series A, AI infra")


def test_resolve_inputs_url() -> None:
    company, seed_url, hint = _resolve_inputs("url", "https://acme.ai")
    assert company == "https://acme.ai"
    assert seed_url == "https://acme.ai"
    assert hint is None


def test_resolve_inputs_url_with_hint() -> None:
    raw = f"https://acme.ai{HINT_SEPARATOR}AI infra, Series A"
    company, seed_url, hint = _resolve_inputs("url", raw)
    assert company == "https://acme.ai"
    assert seed_url == "https://acme.ai"
    assert hint == "AI infra, Series A"


def test_resolve_inputs_deck_rejected() -> None:
    with pytest.raises(PipelineError, match="Phase 5"):
        _resolve_inputs("deck", "any")


def test_resolve_inputs_unknown_type_rejected() -> None:
    with pytest.raises(PipelineError, match="Unknown input_type"):
        _resolve_inputs("garbage", "Acme")


# ---------------------------------------------------------------------------
# _brief_to_markdown
# ---------------------------------------------------------------------------


def test_brief_to_markdown_renders_six_sections() -> None:
    c = BriefClaim(
        claim_text="Founded 2024", quoted_span="founded 2024", source_chunk_id="x"
    )
    brief = Brief(
        founders=[c],
        company=[c],
        market=[c],
        product=[c],
        risk_flags=[c],
        suggested_questions=[c],
    )
    md = _brief_to_markdown(brief, [])
    for heading in (
        "## Founders",
        "## Company",
        "## Market",
        "## Product",
        "## Risk Flags",
        "## Suggested Questions",
    ):
        assert heading in md
    # Every claim renders with the bullet prefix.
    assert "- Founded 2024" in md


def test_brief_to_markdown_empty_section_renders_placeholder() -> None:
    brief = Brief(
        founders=[],
        company=[],
        market=[],
        product=[],
        risk_flags=[],
        suggested_questions=[],
    )
    md = _brief_to_markdown(brief, [])
    assert "_No claims synthesized for this section._" in md


# ---------------------------------------------------------------------------
# run_investigation — happy path / status progression
# ---------------------------------------------------------------------------


def test_happy_path_progresses_status_through_full_enum(patched_pipeline) -> None:
    inv_id = uuid4()
    engine = _FakeEngine(
        SimpleNamespace(input_type="name", input_ref="Acme AI")
    )

    patched_pipeline.setattr(pipeline_mod, "_gather", lambda *a, **kw: [])
    patched_pipeline.setattr(pipeline_mod, "ingest_tool_results", lambda *a, **kw: None)
    patched_pipeline.setattr(pipeline_mod, "_retrieve_all_sections", lambda *a, **kw: [])
    c = BriefClaim(claim_text="x", quoted_span="y", source_chunk_id="z")
    brief = Brief(
        founders=[c],
        company=[c],
        market=[c],
        product=[c],
        risk_flags=[c],
        suggested_questions=[c],
    )
    patched_pipeline.setattr(pipeline_mod, "synthesize_brief", lambda *a, **kw: brief)
    patched_pipeline.setattr(pipeline_mod, "ground_claims", lambda *a, **kw: None)

    run_investigation(inv_id, engine=engine)

    statuses = [u.get("s") for u in engine.conn.updates if "s" in u]
    # D-16 requires the full enum sequence: gathering → synthesizing → grounding → complete.
    assert "gathering" in statuses
    assert "synthesizing" in statuses
    assert "grounding" in statuses
    assert statuses[-1] == "complete"
    # trace_id is recorded on the first status update (CONTEXT.md §Integration Points).
    tids = [u.get("tid") for u in engine.conn.updates]
    assert any(t == "fake-trace-1" for t in tids)


def test_happy_path_writes_brief_markdown(patched_pipeline) -> None:
    inv_id = uuid4()
    engine = _FakeEngine(SimpleNamespace(input_type="name", input_ref="Acme AI"))

    patched_pipeline.setattr(pipeline_mod, "_gather", lambda *a, **kw: [])
    patched_pipeline.setattr(pipeline_mod, "ingest_tool_results", lambda *a, **kw: None)
    patched_pipeline.setattr(pipeline_mod, "_retrieve_all_sections", lambda *a, **kw: [])
    c = BriefClaim(claim_text="founded 2024", quoted_span="founded 2024", source_chunk_id="z")
    brief = Brief(
        founders=[c],
        company=[c],
        market=[c],
        product=[c],
        risk_flags=[c],
        suggested_questions=[c],
    )
    patched_pipeline.setattr(pipeline_mod, "synthesize_brief", lambda *a, **kw: brief)
    patched_pipeline.setattr(pipeline_mod, "ground_claims", lambda *a, **kw: None)

    run_investigation(inv_id, engine=engine)

    complete_update = next(
        u for u in reversed(engine.conn.updates) if u.get("s") == "complete"
    )
    assert complete_update["md"] is not None
    assert "## Founders" in complete_update["md"]
    assert "- founded 2024" in complete_update["md"]


# ---------------------------------------------------------------------------
# run_investigation — failure paths
# ---------------------------------------------------------------------------


def test_synthesize_pipeline_error_sets_failed(patched_pipeline) -> None:
    inv_id = uuid4()
    engine = _FakeEngine(SimpleNamespace(input_type="name", input_ref="Acme AI"))

    patched_pipeline.setattr(pipeline_mod, "_gather", lambda *a, **kw: [])
    patched_pipeline.setattr(pipeline_mod, "ingest_tool_results", lambda *a, **kw: None)
    patched_pipeline.setattr(pipeline_mod, "_retrieve_all_sections", lambda *a, **kw: [])

    def boom(*a, **kw):
        raise PipelineError("synth went boom")

    patched_pipeline.setattr(pipeline_mod, "synthesize_brief", boom)

    run_investigation(inv_id, engine=engine)

    last = engine.conn.updates[-1]
    assert last["s"] == "failed"
    assert "synth went boom" in (last["err"] or "")


def test_unexpected_exception_sets_failed_with_type_prefix(patched_pipeline) -> None:
    inv_id = uuid4()
    engine = _FakeEngine(SimpleNamespace(input_type="name", input_ref="Acme AI"))

    patched_pipeline.setattr(pipeline_mod, "_gather", lambda *a, **kw: [])
    patched_pipeline.setattr(pipeline_mod, "ingest_tool_results", lambda *a, **kw: None)
    patched_pipeline.setattr(pipeline_mod, "_retrieve_all_sections", lambda *a, **kw: [])

    def boom(*a, **kw):
        raise ValueError("unexpected boom")

    patched_pipeline.setattr(pipeline_mod, "synthesize_brief", boom)

    run_investigation(inv_id, engine=engine)

    last = engine.conn.updates[-1]
    assert last["s"] == "failed"
    # Unexpected (non-PipelineError) exceptions get a TypeName: prefix so failures are triageable.
    assert "ValueError" in (last["err"] or "")
    assert "unexpected boom" in (last["err"] or "")


def test_missing_investigation_row_fails_without_raising(patched_pipeline) -> None:
    inv_id = uuid4()
    engine = _FakeEngine(None)  # SELECT returns None

    # run_investigation is called from BackgroundTasks — it must not raise.
    run_investigation(inv_id, engine=engine)

    assert any(u.get("s") == "failed" for u in engine.conn.updates)
    assert any("not found" in (u.get("err") or "") for u in engine.conn.updates)


def test_deck_input_type_fails_fast(patched_pipeline) -> None:
    """Phase 2 rejects 'deck' — it's Phase 5 scope."""
    inv_id = uuid4()
    engine = _FakeEngine(SimpleNamespace(input_type="deck", input_ref="something"))

    run_investigation(inv_id, engine=engine)

    last = engine.conn.updates[-1]
    assert last["s"] == "failed"
    assert "Phase 5" in (last["err"] or "")


# ---------------------------------------------------------------------------
# Firecrawl budget hygiene (T-02-08-04 mitigation)
# ---------------------------------------------------------------------------


def test_firecrawl_budget_popped_on_success(patched_pipeline) -> None:
    inv_id = uuid4()
    inv_key = str(inv_id)
    # Simulate a prior crawl having consumed the budget for this investigation.
    firecrawl_tool._BUDGET[inv_key] = 1
    try:
        engine = _FakeEngine(SimpleNamespace(input_type="name", input_ref="Acme"))
        patched_pipeline.setattr(pipeline_mod, "_gather", lambda *a, **kw: [])
        patched_pipeline.setattr(pipeline_mod, "ingest_tool_results", lambda *a, **kw: None)
        patched_pipeline.setattr(pipeline_mod, "_retrieve_all_sections", lambda *a, **kw: [])
        c = BriefClaim(claim_text="x", quoted_span="y", source_chunk_id="z")
        brief = Brief(
            founders=[c],
            company=[c],
            market=[c],
            product=[c],
            risk_flags=[c],
            suggested_questions=[c],
        )
        patched_pipeline.setattr(pipeline_mod, "synthesize_brief", lambda *a, **kw: brief)
        patched_pipeline.setattr(pipeline_mod, "ground_claims", lambda *a, **kw: None)

        run_investigation(inv_id, engine=engine)
        assert inv_key not in firecrawl_tool._BUDGET
    finally:
        firecrawl_tool._BUDGET.pop(inv_key, None)


def test_firecrawl_budget_popped_on_failure(patched_pipeline) -> None:
    """T-02-08-04: a crashing pipeline must NOT leak stale budget entries."""
    inv_id = uuid4()
    inv_key = str(inv_id)
    firecrawl_tool._BUDGET[inv_key] = 1
    try:
        engine = _FakeEngine(SimpleNamespace(input_type="name", input_ref="Acme"))
        patched_pipeline.setattr(pipeline_mod, "_gather", lambda *a, **kw: [])
        patched_pipeline.setattr(pipeline_mod, "ingest_tool_results", lambda *a, **kw: None)
        patched_pipeline.setattr(pipeline_mod, "_retrieve_all_sections", lambda *a, **kw: [])

        def boom(*a, **kw):
            raise PipelineError("synth went boom")

        patched_pipeline.setattr(pipeline_mod, "synthesize_brief", boom)

        run_investigation(inv_id, engine=engine)
        assert inv_key not in firecrawl_tool._BUDGET
    finally:
        firecrawl_tool._BUDGET.pop(inv_key, None)
