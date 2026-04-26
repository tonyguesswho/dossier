"""pgvector top-k retrieval scoped to one investigation.

Operator: `<=>` (cosine distance) is the only one that hits the HNSW index
created with `vector_cosine_ops`. `<->` (L2) or `<#>` (inner product) fall
back to sequential scan, which blows the latency budget on real corpora.
"""
from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.core.llm import embedding_client, embedding_model

logger = logging.getLogger(__name__)

DEFAULT_TOP_K: int = 6


class RetrievedChunk(BaseModel):
    """distance is raw pgvector `<=>` output: smaller = more similar (0 = identical)."""

    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    source_id: UUID
    url: str
    text: str
    char_start: int
    char_end: int
    distance: float


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def _embed_query(query: str) -> list[float]:
    """Direct OpenAI — OpenRouter doesn't proxy /v1/embeddings.
    Module-scoped so tests can monkeypatch with a fixed vector.
    """
    client = embedding_client()
    resp = client.embeddings.create(model=embedding_model(), input=[query])
    return resp.data[0].embedding


def retrieve_top_k(
    investigation_id: UUID,
    query: str,
    *,
    k: int = DEFAULT_TOP_K,
    engine: Engine | None = None,
) -> list[RetrievedChunk]:
    """Top-k chunks scoped to investigation_id, closest first.

    The WHERE s.investigation_id clause is mandatory — without it, retrieval
    leaks across investigations (covered by test_retrieve_top_k_scoped_to_investigation).
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
