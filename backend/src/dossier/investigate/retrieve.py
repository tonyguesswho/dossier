from __future__ import annotations

import logging
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.core.llm import embed

logger = logging.getLogger(__name__)

DEFAULT_TOP_K: int = 6


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chunk_id: UUID
    source_id: UUID
    url: str
    text: str
    char_start: int
    char_end: int
    # Raw pgvector `<=>` output: smaller = more similar (0 = identical).
    distance: float


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def _embed_query(query: str) -> list[float]:
    # Module-scoped so tests can monkeypatch with a fixed vector.
    return embed([query])[0]


def retrieve_top_k(
    investigation_id: UUID,
    query: str,
    *,
    k: int = DEFAULT_TOP_K,
    engine: Engine | None = None,
) -> list[RetrievedChunk]:
    if not query or not query.strip():
        return []

    query_embedding = _embed_query(query)
    eng = engine if engine is not None else get_engine()

    # `<=>` (cosine distance) is the only operator that hits the HNSW index
    # built with vector_cosine_ops; `<->` and `<#>` fall back to seq scan.
    # The investigation_id WHERE clause is mandatory — without it, retrieval leaks across investigations.
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
