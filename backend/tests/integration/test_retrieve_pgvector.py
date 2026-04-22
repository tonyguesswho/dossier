"""Integration test for retrieve_top_k against real pgvector.

Runs against the docker-compose postgres service (DATABASE_URL in .env).
Seeds one investigation's worth of sources + chunks, then queries.

Gated by @pytest.mark.integration per backend/pyproject.toml markers list.
Skipped automatically if DATABASE_URL is not set.

Target runtime: <5s (real DB, mocked query embedding via monkeypatch).
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import text

from dossier.core import db as db_module
from dossier.investigate import retrieve as retrieve_module
from dossier.investigate.retrieve import retrieve_top_k

pytestmark = pytest.mark.integration


def _require_db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skipping integration test")
    return url


def _seed_one_chunk(
    engine, investigation_id: uuid.UUID, embedding: list[float], chunk_text: str
) -> uuid.UUID:
    """Insert a users row + investigation + source + 1 chunk. Returns chunk_id."""
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id) VALUES (:u) ON CONFLICT DO NOTHING"),
            {"u": "test-user-ret"},
        )
        conn.execute(
            text(
                "INSERT INTO investigations (id, user_id, status, input_type, input_ref) "
                "VALUES (:id, :u, 'gathering', 'name', 'Acme AI') ON CONFLICT DO NOTHING"
            ),
            {"id": str(investigation_id), "u": "test-user-ret"},
        )
        src_row = conn.execute(
            text(
                "INSERT INTO sources (investigation_id, url, content_hash, raw_text_s3_key, "
                "source_kind, metadata) "
                "VALUES (:inv, :url, :h, :k, 'web', '{}'::jsonb) RETURNING id"
            ),
            {
                "inv": str(investigation_id),
                "url": f"https://example.com/{uuid.uuid4()}",
                "h": str(uuid.uuid4()),
                "k": "local://test",
            },
        ).fetchone()
        src_id = src_row.id
        vec_lit = "[" + ",".join(f"{v:.7f}" for v in embedding) + "]"
        chunk_row = conn.execute(
            text(
                "INSERT INTO source_chunks "
                "(source_id, chunk_index, text, char_start, char_end, embedding, metadata) "
                "VALUES (:s, 0, :t, 0, :ce, CAST(:e AS VECTOR(1536)), CAST(:m AS JSONB)) "
                "RETURNING id"
            ),
            {
                "s": src_id,
                "t": chunk_text,
                "ce": len(chunk_text),
                "e": vec_lit,
                "m": json.dumps({"embedding_model": "text-embedding-3-small"}),
            },
        ).fetchone()
        return chunk_row.id


def _cleanup(engine, investigation_id: uuid.UUID) -> None:
    """Delete the investigation and the test user ONLY if no other investigations remain.

    Guards against FK violation when two scoping-test investigations share the same
    test user — deleting the user after the first investigation would cascade-fail
    while the second investigation still references it.
    """
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM investigations WHERE id = :id"), {"id": str(investigation_id)}
        )
        conn.execute(
            text(
                "DELETE FROM users WHERE id = 'test-user-ret' "
                "AND NOT EXISTS (SELECT 1 FROM investigations WHERE user_id = 'test-user-ret')"
            )
        )


# ---------------------------------------------------------------------------
# Case 1: ranking — closer vectors must come first
# ---------------------------------------------------------------------------
def test_retrieve_top_k_ranks_closer_vectors_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_db_url()
    db_module._reset_engine_for_tests()
    engine = db_module.get_engine()

    inv_id = uuid.uuid4()

    # 3 chunks, progressively farther from the query vector.
    near = [0.0] * 1536
    near[0] = 1.0
    mid = [0.0] * 1536
    mid[0] = 0.5
    mid[100] = 0.5
    far = [0.0] * 1536
    far[500] = 1.0

    near_id = _seed_one_chunk(engine, inv_id, near, "Acme founded in 2024")
    _seed_one_chunk(engine, inv_id, mid, "Half-way content")
    _seed_one_chunk(engine, inv_id, far, "Completely unrelated")

    # Monkeypatch the query embedder to return the "near" vector so it ranks first.
    monkeypatch.setattr(retrieve_module, "_embed_query", lambda _q: near)

    try:
        results = retrieve_top_k(inv_id, "founders Acme", k=3)
        assert len(results) == 3
        assert results[0].chunk_id == near_id
        assert results[0].distance <= results[1].distance <= results[2].distance
    finally:
        _cleanup(engine, inv_id)


# ---------------------------------------------------------------------------
# Case 2: scoping — chunks from a different investigation MUST NOT appear.
# Defense for T-02-06-03 (cross-investigation chunk leak).
# ---------------------------------------------------------------------------
def test_retrieve_top_k_scoped_to_investigation(monkeypatch: pytest.MonkeyPatch) -> None:
    _require_db_url()
    db_module._reset_engine_for_tests()
    engine = db_module.get_engine()

    inv_a = uuid.uuid4()
    inv_b = uuid.uuid4()
    vec = [0.0] * 1536
    vec[0] = 1.0

    _seed_one_chunk(engine, inv_a, vec, "investigation A chunk")
    _seed_one_chunk(engine, inv_b, vec, "investigation B chunk — must not leak")

    monkeypatch.setattr(retrieve_module, "_embed_query", lambda _q: vec)

    try:
        results = retrieve_top_k(inv_a, "any query", k=5)
        # All returned chunks are from investigation A's sources
        assert all("investigation B chunk" not in r.text for r in results)
    finally:
        _cleanup(engine, inv_a)
        _cleanup(engine, inv_b)


# ---------------------------------------------------------------------------
# Case 3: empty investigation (no chunks) returns empty list (not an error).
# D-09 §Claude's Discretion fail-open policy.
# ---------------------------------------------------------------------------
def test_retrieve_top_k_empty_investigation_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_db_url()
    db_module._reset_engine_for_tests()
    engine = db_module.get_engine()

    inv_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id) VALUES (:u) ON CONFLICT DO NOTHING"),
            {"u": "test-user-ret"},
        )
        conn.execute(
            text(
                "INSERT INTO investigations (id, user_id, status, input_type, input_ref) "
                "VALUES (:id, :u, 'gathering', 'name', 'empty') ON CONFLICT DO NOTHING"
            ),
            {"id": str(inv_id), "u": "test-user-ret"},
        )

    monkeypatch.setattr(retrieve_module, "_embed_query", lambda _q: [0.0] * 1536)

    try:
        results = retrieve_top_k(inv_id, "anything", k=6)
        assert results == []
    finally:
        _cleanup(engine, inv_id)


# ---------------------------------------------------------------------------
# Case 4: empty query short-circuits before any DB or embedding call.
# ---------------------------------------------------------------------------
def test_retrieve_top_k_empty_query_returns_empty() -> None:
    inv_id = uuid.uuid4()
    assert retrieve_top_k(inv_id, "") == []
    assert retrieve_top_k(inv_id, "   \t\n  ") == []
