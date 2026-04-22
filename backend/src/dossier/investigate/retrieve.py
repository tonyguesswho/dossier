"""pgvector top-k retrieval for Phase 2 synthesis.

One function: `retrieve_top_k(investigation_id, query, k=6)`.
Section-biasing via query reformulation (e.g., "founders of Acme AI" vs.
"market size for Acme AI"), not metadata filter. Re-rank deferred to Phase 3.

Query operator: `<=>` is pgvector's cosine-distance operator — it hits the
HNSW index created by migration 0001 with `vector_cosine_ops`. If we used
`<->` (L2 distance) or `<#>` (inner product), the query would NOT hit the
index and would fall back to sequential scan, making Phase 2 retrieval
slow enough to blow the 210s pipeline budget (CONTEXT.md §specifics).

Migration 0001 lines 161-167 created:
    CREATE INDEX source_chunks_embedding_idx ON source_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m=16, ef_construction=64)

Rejected alternatives:
  - Re-rank with cross-encoder / BM25 hybrid: Phase 2 keeps retrieval simple
    per CONTEXT.md D-04 (linear pipeline). Phase 3 LangGraph adds re-rank.
  - Metadata filter on source_kind: callers who want "only github sources"
    can filter in Python; keeping the SQL one-query simplifies Phase 4 eval
    replay (deterministic SQL = deterministic retrieval).
  - Top-k = 4 (smaller): CONTEXT.md §Claude's Discretion suggests 4-6;
    pick 6 for recall headroom per PATTERNS.md §retrieve.py.
  - `<->` L2 distance: would NOT hit the vector_cosine_ops HNSW index,
    forcing a sequential scan. Cosine distance (`<=>`) is the locked
    operator for this schema.

Exported contract (imported by Plan 02-07 retrieval wrapper + Plan 02-08 synth):
  - RetrievedChunk Pydantic model
  - retrieve_top_k function
  - DEFAULT_TOP_K constant
"""
from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.core.llm import embedding_model, strong_model

logger = logging.getLogger(__name__)

# top-k = 6 per CONTEXT.md §Claude's Discretion (4-6 range; pick 6 for recall headroom).
DEFAULT_TOP_K: int = 6


class RetrievedChunk(BaseModel):
    """One chunk returned by retrieve_top_k, ranked by pgvector cosine distance.

    distance is the raw pgvector `<=>` output: smaller = more similar (0 = identical).
    Downstream synth code uses distance only for ranking, not as a calibrated score.
    """

    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    source_id: UUID
    url: str
    text: str
    char_start: int
    char_end: int
    distance: float


def _vector_literal(vec: list[float]) -> str:
    """Format a Python list[float] as a pgvector `[x,y,z]` string literal.

    Duplicated from ingest._vector_literal (intentionally — different modules
    serialize vectors for different directions: ingest writes, retrieve queries).
    If this formatter ever needs semantic changes, both locations must update.
    """
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def _embed_query(query: str) -> list[float]:
    """Embed a single query string via text-embedding-3-small (through OpenRouter).

    Exposed at module scope (not inlined in retrieve_top_k) so tests can
    monkeypatch with a fixed vector — see test_retrieve_pgvector.py.
    """
    client = strong_model()
    resp = client.embeddings.create(model=embedding_model(), input=[query])
    return resp.data[0].embedding


def retrieve_top_k(
    investigation_id: UUID,
    query: str,
    *,
    k: int = DEFAULT_TOP_K,
    engine: Engine | None = None,
) -> list[RetrievedChunk]:
    """Return the top-k source_chunks scoped to investigation_id, closest first.

    Empty list if the investigation has no chunks — caller decides whether that
    is an error or a thin-brief scenario (CONTEXT.md §Claude's Discretion
    fail-open: partial brief > failed investigation).

    Security: WHERE s.investigation_id = :inv is MANDATORY — defends against
    T-02-06-03 (cross-investigation chunk leak). Covered by
    test_retrieve_top_k_scoped_to_investigation.
    """
    if not query or not query.strip():
        return []

    query_embedding = _embed_query(query)
    eng = engine if engine is not None else get_engine()

    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    sc.id          AS chunk_id,
                    sc.source_id   AS source_id,
                    s.url          AS url,
                    sc.text        AS chunk_text,
                    sc.char_start  AS char_start,
                    sc.char_end    AS char_end,
                    (sc.embedding <=> CAST(:qe AS VECTOR(1536))) AS distance
                FROM source_chunks sc
                JOIN sources s ON s.id = sc.source_id
                WHERE s.investigation_id = :inv
                ORDER BY sc.embedding <=> CAST(:qe AS VECTOR(1536))
                LIMIT :k
                """
            ),
            {
                "inv": str(investigation_id),
                "qe": _vector_literal(query_embedding),
                "k": k,
            },
        ).fetchall()

    return [
        RetrievedChunk(
            chunk_id=row.chunk_id,
            source_id=row.source_id,
            url=row.url,
            text=row.chunk_text,
            char_start=row.char_start,
            char_end=row.char_end,
            distance=float(row.distance),
        )
        for row in rows
    ]


__all__ = ["DEFAULT_TOP_K", "RetrievedChunk", "retrieve_top_k"]
