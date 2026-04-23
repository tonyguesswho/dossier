"""Pitch-deck ingestion: PDF → markdown → pipeline (Phase 5-lite).

MarkItDown (microsoft/markitdown) handles PDF parsing via pdfminer.six. For
well-formatted PDFs (reports, whitepapers) output is clean; for pitch decks
with stylized text boxes, layout can be jumbled. Acceptable tradeoff for the
demo-lite path — vision extraction is roadmapped.

Design notes:
  - One synthetic ToolResult wraps the full markdown → fed to ingest_tool_results
    which chunks at 800 tokens / 120 overlap (same as web sources). Pitch-deck
    bullets often chunk well because MarkItDown preserves page-break paragraphs.
  - source_kind="deck_page" so the sources.source_kind CHECK constraint and
    frontend SourceListItem type (which already enumerates "deck_page") both
    accept the row. ToolResult.source_kind Literal was widened in this plan.
  - We pre-populate source_chunks BEFORE calling run_investigation so the
    pipeline's _gather stage can still run against the filename stub (returns
    empty), then _retrieve sees the deck chunks and synthesis proceeds normally.
  - Dispatch is always local (FastAPI BackgroundTasks). Lambda self-invoke for
    decks would need S3 to ferry the markdown — out of scope for demo-lite.

Rejected alternatives:
  - Skip run_investigation and call the downstream stages (retrieve, synth,
    ground, render) inline: duplicates pipeline.py's Langfuse wiring + status
    transitions + brief rendering. Reuse-is-cheaper.
  - Use MarkItDown on raw bytes without BytesIO wrapper: convert_stream() wants
    a BinaryIO; a bytes object triggers AttributeError on .read().
  - Vision model (GPT-4o) on rasterized slides: Phase 5-full path; adds
    pdf2image + poppler + per-slide OpenAI calls. Out of scope for demo-lite.
"""
from __future__ import annotations

import io
import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.investigate.ingest import ingest_tool_results
from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)


def pdf_to_markdown(pdf_bytes: bytes, filename: str) -> str:
    """Convert PDF bytes to markdown via MarkItDown. Returns extracted text.

    `filename` is cosmetic — MarkItDown infers the parser from ``file_extension``
    and/or magic bytes. We pass ``.pdf`` explicitly to avoid the sniffing step.
    Deferred import keeps module-load cheap for tests that never hit this path.
    """
    from markitdown import MarkItDown  # noqa: PLC0415 — deferred import

    md = MarkItDown()
    stream = io.BytesIO(pdf_bytes)
    result = md.convert_stream(stream, file_extension=".pdf")
    return result.text_content or ""


def run_deck_investigation(
    investigation_id: UUID,
    markdown_text: str,
    filename: str,
    *,
    engine: Engine | None = None,
) -> None:
    """Run the investigation pipeline with a pitch deck as the sole source.

    Flow:
      1. Synthesize one ToolResult wrapping the uploaded markdown.
      2. ingest_tool_results → chunk + embed + INSERT sources + source_chunks.
      3. Delegate to run_investigation, which handles:
         gather (noop/empty for decks) → ingest (empty list, no-op) →
         retrieve (finds pre-seeded deck chunks) → synthesize → ground →
         render brief_markdown → status=complete.

    On ingest failure the investigation is marked failed and we return early;
    run_investigation is never called with a half-populated chunk table.
    """
    # Deferred import of pipeline so test harness can monkeypatch freely and
    # to match the pipeline module's own deferred-langfuse import style.
    from dossier.investigate.pipeline import run_investigation  # noqa: PLC0415

    eng = engine if engine is not None else get_engine()

    deck_url = f"upload://{investigation_id}/{filename}"
    synthetic_result = ToolResult(
        url=deck_url,
        source_kind="deck_page",
        text=markdown_text,
        title=filename,
        raw_metadata={"extraction": "markitdown"},
    )

    try:
        ingest_tool_results(investigation_id, [synthetic_result], engine=eng)
    except Exception:
        logger.exception("run_deck_investigation: pre-ingest failed")
        with eng.begin() as conn:
            conn.execute(
                text(
                    "UPDATE investigations "
                    "SET status = CAST('failed' AS investigation_status), "
                    "    error = :e, "
                    "    completed_at = now() "
                    "WHERE id = :i"
                ),
                {"e": "deck ingest failed", "i": str(investigation_id)},
            )
        return

    run_investigation(investigation_id, engine=eng)


__all__ = ["pdf_to_markdown", "run_deck_investigation"]
