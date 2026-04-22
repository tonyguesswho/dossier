"""Chunk + embed + insert pipeline stage.

Takes list[ToolResult] (from dossier.investigate.tools.*) and writes:
  - One `sources` row per ToolResult (deduped by content_hash per investigation).
  - Many `source_chunks` rows per source — chunked 800 tokens / 120 overlap.

Pitfall defenses:
  - Pitfall 2.1 (chunking severs claims): use RecursiveCharacterTextSplitter with
    tiktoken encoder so 800/120 are TOKEN counts (not character counts). A raw
    character splitter at 800 chars would break mid-sentence and sever claims
    from the evidence spans they cite.
  - Pitfall 2.2 (embedding drift): every source_chunks.metadata JSONB must include
    `embedding_model`. If we later switch models, a script can backfill old rows
    without guessing which embedding produced what.
  - Pitfall 3.5 (in-memory state): all rows go through Postgres immediately;
    nothing stays in Python dicts past function return.

Rejected alternatives:
  - LangChain CharacterTextSplitter (char-based 800): breaks mid-sentence on long
    English paragraphs (Pitfall 2.1 scenario).
  - tiktoken.encode(...) hand-rolled chunking: reimplements a solved problem;
    RecursiveCharacterTextSplitter from langchain-text-splitters already accounts
    for paragraph/sentence boundaries with token-count limits.
  - Store raw text in S3 (Phase 3+): out of scope for Phase 2 (CONTEXT.md D-01
    local backend). Use a synthetic `local://investigations/...` placeholder in
    raw_text_s3_key; Phase 5 deck upload will wire real S3.
  - Skip content_hash (accept duplicates): Exa + Firecrawl on the same domain
    return overlapping URLs often. content_hash dedupe is cheap and correct.
  - `from_tiktoken_encoder()` with default encoding (gpt2): triggers a network
    download of gpt-2 vocab.bpe on first call — fails in offline/sandboxed envs.
    We pass `encoding_name="cl100k_base"` explicitly; it's cl100k that
    text-embedding-3-small actually uses AND it ships bundled with tiktoken.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.core.llm import EMBEDDING_MODEL_ID, embedding_model, strong_model
from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)

# STACK.md §2.2 / Pitfall 2.1 defense — 800/120 in TOKENS (not chars).
CHUNK_SIZE_TOKENS: int = 800
CHUNK_OVERLAP_TOKENS: int = 120

# OpenAI embeddings endpoint accepts up to ~100 inputs per call efficiently
# (ARCHITECTURE.md §10 "OpenAI embeddings" row).
EMBED_BATCH_SIZE: int = 100

# cl100k_base is the encoding used by text-embedding-3-small (and gpt-4, gpt-3.5-turbo).
# Hardcoded rather than defaulted because `from_tiktoken_encoder()`'s default `gpt2`
# requires a network download on first use; cl100k_base ships bundled with tiktoken.
_TIKTOKEN_ENCODING: str = "cl100k_base"


@dataclass(frozen=True)
class ChunkSpan:
    """One chunk with its character offsets in the source text."""

    chunk_index: int
    text: str
    char_start: int
    char_end: int


class IngestStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sources_inserted: int = 0
    chunks_inserted: int = 0
    chunks_skipped_empty: int = 0
    duplicate_sources_skipped: int = 0


def _build_splitter():
    """RecursiveCharacterTextSplitter in tiktoken token-count mode."""
    # Deferred import: keep module import cheap for tests that don't touch chunking.
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: PLC0415

    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=_TIKTOKEN_ENCODING,
        chunk_size=CHUNK_SIZE_TOKENS,
        chunk_overlap=CHUNK_OVERLAP_TOKENS,
    )


def _chunk_text(full_text: str) -> list[ChunkSpan]:
    """Split text into token-bounded chunks with char_start/char_end offsets.

    The splitter returns substrings; we find each substring's position in the
    original text for the char offsets (INVEST-04 stable span requirement).
    Overlapping chunks share characters — that's fine; each chunk's offsets
    are independently valid for quoting.
    """
    if not full_text or not full_text.strip():
        return []
    splitter = _build_splitter()
    parts: list[str] = splitter.split_text(full_text)

    spans: list[ChunkSpan] = []
    cursor = 0
    for i, part in enumerate(parts):
        # Find this chunk starting from `cursor` — prevents earlier-identical-phrase
        # false matches. If not found forward, fall back to a full-text find.
        found = full_text.find(part, cursor)
        if found == -1:
            found = full_text.find(part)
        if found == -1:
            # Shouldn't happen with RecursiveCharacterTextSplitter, but be safe.
            logger.warning("Chunk %d text not located in source; char_start=0", i)
            found = 0
        spans.append(
            ChunkSpan(
                chunk_index=i,
                text=part,
                char_start=found,
                char_end=found + len(part),
            )
        )
        cursor = found + 1  # advance past current start for next search
    return spans


def _embed_chunks(chunk_texts: list[str]) -> list[list[float]]:
    """Batch-embed chunk texts via OpenAI text-embedding-3-small (through OpenRouter)."""
    if not chunk_texts:
        return []
    client = strong_model()
    model = embedding_model()
    embeddings: list[list[float]] = []
    for i in range(0, len(chunk_texts), EMBED_BATCH_SIZE):
        batch = chunk_texts[i : i + EMBED_BATCH_SIZE]
        response = client.embeddings.create(model=model, input=batch)
        embeddings.extend([d.embedding for d in response.data])
    return embeddings


def _sha256(text_value: str) -> str:
    """Return the hex SHA-256 of a string. Used for sources.content_hash dedup."""
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _vector_literal(vec: list[float]) -> str:
    """Format a Python list[float] as a pgvector `[x,y,z]` string literal.

    7-digit precision matches text-embedding-3-small's float32 range and keeps
    INSERT payloads compact without meaningful precision loss for cosine similarity.
    """
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def ingest_tool_results(
    investigation_id: UUID,
    results: list[ToolResult],
    *,
    engine: Engine | None = None,
) -> IngestStats:
    """Write one sources row + N source_chunks rows per ToolResult.

    Contract:
      - Empty-text results are skipped (logged, counted in chunks_skipped_empty).
      - Duplicate (investigation_id, content_hash) rows are skipped via UNIQUE
        constraint ON CONFLICT DO NOTHING; counted in duplicate_sources_skipped.
      - Every source_chunks row stores `embedding_model` in metadata JSONB
        (Pitfall 2.2 defense).

    Returns IngestStats with counters.
    """
    stats = IngestStats()
    if not results:
        return stats

    eng = engine if engine is not None else get_engine()

    with eng.begin() as conn:
        for result in results:
            if not result.text or not result.text.strip():
                logger.info("Skipping empty-text tool result: %s", result.url)
                stats.chunks_skipped_empty += 1
                continue

            content_hash = _sha256(result.text)

            # Upsert source row. ON CONFLICT on (investigation_id, content_hash).
            src_row = conn.execute(
                text(
                    """
                    INSERT INTO sources
                        (investigation_id, url, content_hash, raw_text_s3_key,
                         source_kind, metadata)
                    VALUES
                        (:inv, :url, :hash,
                         :s3key,
                         :kind, CAST(:meta AS JSONB))
                    ON CONFLICT (investigation_id, content_hash) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "inv": str(investigation_id),
                    "url": result.url,
                    "hash": content_hash,
                    # Phase 2 local: synthetic s3 key placeholder (D-01). Phase 5 wires real S3.
                    "s3key": f"local://investigations/{investigation_id}/{content_hash}.txt",
                    "kind": result.source_kind,
                    "meta": json.dumps(
                        {
                            "title": result.title,
                            "fetched_at": result.fetched_at.isoformat(),
                            **(result.raw_metadata or {}),
                        }
                    ),
                },
            ).fetchone()

            if src_row is None:
                # ON CONFLICT path — source already exists; don't re-chunk.
                stats.duplicate_sources_skipped += 1
                continue

            source_id = src_row.id
            stats.sources_inserted += 1

            chunks = _chunk_text(result.text)
            if not chunks:
                stats.chunks_skipped_empty += 1
                continue

            # Embed all chunks for this source in batched calls.
            embeddings = _embed_chunks([c.text for c in chunks])

            if len(embeddings) != len(chunks):
                raise RuntimeError(
                    f"Embedding count mismatch: got {len(embeddings)} for {len(chunks)} chunks"
                )

            # Bulk insert chunk rows. Pgvector accepts a vector literal cast.
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                conn.execute(
                    text(
                        """
                        INSERT INTO source_chunks
                            (source_id, chunk_index, text, char_start, char_end,
                             embedding, metadata)
                        VALUES
                            (:src_id, :idx, :txt, :cs, :ce,
                             CAST(:emb AS VECTOR(1536)),
                             CAST(:meta AS JSONB))
                        """
                    ),
                    {
                        "src_id": source_id,
                        "idx": chunk.chunk_index,
                        "txt": chunk.text,
                        "cs": chunk.char_start,
                        "ce": chunk.char_end,
                        # Pitfall 2.2 defense — stamp embedding model on every chunk.
                        "meta": json.dumps({"embedding_model": EMBEDDING_MODEL_ID}),
                        "emb": _vector_literal(embedding),
                    },
                )
            stats.chunks_inserted += len(chunks)

    return stats


__all__ = [
    "CHUNK_OVERLAP_TOKENS",
    "CHUNK_SIZE_TOKENS",
    "ChunkSpan",
    "IngestStats",
    "ingest_tool_results",
]
