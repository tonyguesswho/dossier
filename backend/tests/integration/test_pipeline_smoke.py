"""Integration smoke test for pipeline.run_investigation against real pgvector.

External dependencies (OpenAI/OpenRouter, Exa, GitHub, Firecrawl, Langfuse) are
mocked. Postgres is real — this test verifies the end-to-end INSERT/SELECT SQL
paths match migrations 0001 + 0002 and that the full sources → source_chunks
→ claims → investigations write-chain is coherent.

Gated by @pytest.mark.integration. Skipped when DATABASE_URL is unset.
Target runtime: <10s (SLO reference for pipeline 02-CONTEXT.md §specifics is
210s nominal — this test is orders of magnitude faster because the 60s synth LLM
call and the 90s tool fetches are mocked).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from dossier.core import db as db_module
from dossier.investigate import ingest as ingest_mod
from dossier.investigate import pipeline as pipeline_mod
from dossier.investigate import retrieve as retrieve_mod
from dossier.investigate.pipeline import run_investigation
from dossier.investigate.tools import exa as exa_tool
from dossier.investigate.tools import firecrawl as firecrawl_tool
from dossier.investigate.tools import github as github_tool
from dossier.investigate.tools.types import ToolResult
from dossier.models import Brief, BriefClaim
from dossier.observability import load_env

pytestmark = pytest.mark.integration


def _require_db_url() -> None:
    """Load .env first (matches db.read_database_url pattern) then check."""
    load_env()
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skipping integration test")


def _seeded_vector(seed: str) -> list[float]:
    """Deterministic 1536-dim 'vector' derived from a seed string.

    Tile a 32-byte SHA-256 hash to 1536 floats in [-1, 1]. Deterministic so
    pgvector HNSW retrieval ordering is stable across test runs.
    """
    h = hashlib.sha256(seed.encode()).digest()
    return [((h[i % 32]) / 255.0 - 0.5) * 2.0 for i in range(1536)]


# ---------------------------------------------------------------------------
# Fake OpenAI client — supplies `.embeddings.create(model=..., input=...)`.
# Callers pass either a list or a single string; return one embedding per input.
# ---------------------------------------------------------------------------


class _FakeEmbResp:
    def __init__(self, texts: list[str]) -> None:
        self.data = [SimpleNamespace(embedding=_seeded_vector(t)) for t in texts]


class _FakeEmbeddings:
    @staticmethod
    def create(model: str, input):  # noqa: A002 — mirror openai SDK signature
        texts = input if isinstance(input, list) else [input]
        return _FakeEmbResp(texts)


class _FakeOpenAI:
    embeddings = _FakeEmbeddings()


@pytest.fixture
def mocked_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Mock every external dep except Postgres."""
    # Langfuse no-op (graceful-degrade path is already covered by observability;
    # here we short-circuit to skip any network call at all).
    class _NoopSpan:
        def update(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _NoopLF:
        def start_as_current_observation(self, **kw):
            return _NoopSpan()

        def get_trace_id(self):
            return "smoke-trace"

        def flush(self):
            pass

        def shutdown(self):
            pass

    monkeypatch.setattr(
        pipeline_mod, "get_langfuse_client", lambda strict=False: _NoopLF()
    )
    monkeypatch.setattr(
        pipeline_mod, "flush_and_shutdown", lambda *a, **kw: None
    )
    # pipeline's `from langfuse import propagate_attributes` — inject a no-op.
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
    monkeypatch.setitem(sys.modules, "langfuse", fake_lf)

    # External tools — return deterministic ToolResults with plausible text so
    # ingest has something real to chunk + embed.
    def fake_exa_search(query, *, num_results=8):
        return [
            ToolResult(
                url="https://acme.ai",
                source_kind="web",
                text=(
                    "Acme AI was founded in 2024 by two Stanford grads. "
                    "The company builds analyst tools for venture capital investors. "
                    "It has raised a seed round from Sequoia and Benchmark."
                ),
                title="Acme AI — About",
            ),
            ToolResult(
                url="https://acme.ai/team",
                source_kind="web",
                text=(
                    "The founding team: Alice (CEO) is an ex-Google ML engineer. "
                    "Bob (CTO) previously built search systems at Meta."
                ),
                title="Acme AI — Team",
            ),
        ]

    monkeypatch.setattr(exa_tool, "search", fake_exa_search)

    def fake_gh(name, *, token=None):
        return [
            ToolResult(
                url="https://github.com/alice",
                source_kind="github",
                text="Alice, ML engineer, ex-Google. Interested in LLM evaluation.",
                title="GitHub · alice",
            )
        ]

    monkeypatch.setattr(github_tool, "fetch_founder_profile", fake_gh)

    def fake_crawl(url, *, investigation_id, _budget_tracker=None):
        return [
            ToolResult(
                url=url,
                source_kind="crawl",
                text=(
                    "Deep-crawl excerpt: Acme's product is enterprise AI infra for "
                    "analyst workflows. Target market is top-quartile venture funds."
                ),
                title="Acme AI — Deep crawl",
            )
        ]

    monkeypatch.setattr(firecrawl_tool, "crawl_seed_url", fake_crawl)

    # OpenAI client used by ingest._embed_chunks and retrieve._embed_query.
    # Both modules call strong_model() to construct the client. We patch at
    # the module level so no real OpenRouter call is attempted.
    monkeypatch.setattr(ingest_mod, "strong_model", lambda: _FakeOpenAI())
    monkeypatch.setattr(retrieve_mod, "strong_model", lambda: _FakeOpenAI())

    # Mock synthesize_brief at the pipeline level (avoids needing a real LLM
    # client shape for beta.chat.completions.parse). The mock builds a Brief
    # with a bogus source_chunk_id so ground.py exercises the "unknown source"
    # branch — which writes with grounded_source_chunk_id=NULL per D-06.
    c = BriefClaim(
        claim_text="Acme AI was founded in 2024 by two Stanford grads.",
        quoted_span="founded in 2024 by two Stanford grads",
        source_chunk_id="bogus",
    )
    brief = Brief(
        founders=[c],
        company=[c],
        market=[c],
        product=[c],
        risk_flags=[c],
        suggested_questions=[c],
    )
    monkeypatch.setattr(
        pipeline_mod,
        "synthesize_brief",
        lambda retrieved, company, context_hint=None: brief,
    )

    return monkeypatch


# ---------------------------------------------------------------------------
# Case 1: happy path — status goes complete, all tables written.
# ---------------------------------------------------------------------------
def test_pipeline_happy_path_writes_all_tables(mocked_pipeline) -> None:  # noqa: ARG001
    _require_db_url()
    db_module._reset_engine_for_tests()
    engine = db_module.get_engine()

    inv_id = uuid.uuid4()
    user_id = "test-pipeline-smoke-user"
    try:
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO users (id) VALUES (:u) ON CONFLICT DO NOTHING"),
                {"u": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO investigations (id, user_id, status, input_type, input_ref) "
                    "VALUES (:id, :u, 'queued', 'name', 'Acme AI')"
                ),
                {"id": str(inv_id), "u": user_id},
            )

        run_investigation(inv_id)

        with engine.connect() as conn:
            inv = conn.execute(
                text(
                    "SELECT status, brief_markdown, error, langfuse_trace_id "
                    "FROM investigations WHERE id = :id"
                ),
                {"id": str(inv_id)},
            ).fetchone()
            assert inv.status == "complete", (
                f"status was {inv.status}; error={inv.error}"
            )
            assert "## Founders" in (inv.brief_markdown or "")
            assert "## Company" in (inv.brief_markdown or "")
            # Trace id is recorded on the first status update (gathering).
            assert inv.langfuse_trace_id == "smoke-trace"

            src_count = conn.execute(
                text("SELECT COUNT(*) FROM sources WHERE investigation_id = :id"),
                {"id": str(inv_id)},
            ).scalar_one()
            # 2 Exa + 1 GitHub + 1 Firecrawl = 4 sources (all unique content_hash).
            assert src_count >= 1

            chunk_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM source_chunks sc "
                    "JOIN sources s ON s.id = sc.source_id "
                    "WHERE s.investigation_id = :id "
                    "AND sc.metadata->>'embedding_model' = 'text-embedding-3-small'"
                ),
                {"id": str(inv_id)},
            ).scalar_one()
            # Pitfall 2.2 defense: every chunk row must stamp embedding_model.
            assert chunk_count >= 1

            claim_count = conn.execute(
                text("SELECT COUNT(*) FROM claims WHERE investigation_id = :id"),
                {"id": str(inv_id)},
            ).scalar_one()
            # 6 sections × 1 claim each from the mocked Brief.
            assert claim_count == 6

            # All claims in this smoke test cite a bogus source_chunk_id, so
            # all 6 land with grounded_source_chunk_id=NULL per D-06.
            null_gid = conn.execute(
                text(
                    "SELECT COUNT(*) FROM claims "
                    "WHERE investigation_id = :id "
                    "AND grounded_source_chunk_id IS NULL"
                ),
                {"id": str(inv_id)},
            ).scalar_one()
            assert null_gid == 6

        # T-02-08-04: Firecrawl budget entry popped after run completes.
        assert str(inv_id) not in firecrawl_tool._BUDGET
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM investigations WHERE id = :id"),
                {"id": str(inv_id)},
            )
            # Only delete user if no other investigations remain under it
            # (defends against shared-user cascade across test files).
            conn.execute(
                text(
                    "DELETE FROM users WHERE id = :u "
                    "AND NOT EXISTS (SELECT 1 FROM investigations WHERE user_id = :u)"
                ),
                {"u": user_id},
            )
        firecrawl_tool._BUDGET.pop(str(inv_id), None)
