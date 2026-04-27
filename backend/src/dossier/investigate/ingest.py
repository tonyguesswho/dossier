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
from dossier.core.llm import EMBEDDING_MODEL_ID, embed
from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)

# Tokens, not chars. Char-based 800 splits mid-sentence on long paragraphs.
CHUNK_SIZE_TOKENS: int = 800
CHUNK_OVERLAP_TOKENS: int = 120

# cl100k_base ships bundled with tiktoken; the default `gpt2` triggers a network download on first call.
_TIKTOKEN_ENCODING: str = "cl100k_base"


@dataclass(frozen=True)
class ChunkSpan:
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
    from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: PLC0415

    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name=_TIKTOKEN_ENCODING,
        chunk_size=CHUNK_SIZE_TOKENS,
        chunk_overlap=CHUNK_OVERLAP_TOKENS,
    )


def _chunk_text(full_text: str) -> list[ChunkSpan]:
    if not full_text or not full_text.strip():
        return []
    splitter = _build_splitter()
    parts: list[str] = splitter.split_text(full_text)

    spans: list[ChunkSpan] = []
    cursor = 0
    for i, part in enumerate(parts):
        # Search forward from cursor first to avoid earlier identical-phrase matches.
        found = full_text.find(part, cursor)
        if found == -1:
            found = full_text.find(part)
        if found == -1:
            logger.warning("Chunk %d not located in source; using char_start=0", i)
            found = 0
        spans.append(
            ChunkSpan(
                chunk_index=i,
                text=part,
                char_start=found,
                char_end=found + len(part),
            )
        )
        cursor = found + 1
    return spans


def _embed_chunks(chunk_texts: list[str]) -> list[list[float]]:
    return embed(chunk_texts)


def _sha256(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def ingest_tool_results(
    investigation_id: UUID,
    results: list[ToolResult],
    *,
    engine: Engine | None = None,
) -> IngestStats:
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
                stats.duplicate_sources_skipped += 1
                continue

            source_id = src_row.id
            stats.sources_inserted += 1

            chunks = _chunk_text(result.text)
            if not chunks:
                stats.chunks_skipped_empty += 1
                continue

            embeddings = _embed_chunks([c.text for c in chunks])

            if len(embeddings) != len(chunks):
                raise RuntimeError(
                    f"Embedding count mismatch: got {len(embeddings)} for {len(chunks)} chunks"
                )

            for chunk, embedding in zip(chunks, embeddings, strict=True):
                # source_chunks.metadata stamps embedding_model so a future model swap
                # has a backfill marker for which embedding produced which row.
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
                        "meta": json.dumps({"embedding_model": EMBEDDING_MODEL_ID}),
                        "emb": _vector_literal(embedding),
                    },
                )
            stats.chunks_inserted += len(chunks)

    return stats

