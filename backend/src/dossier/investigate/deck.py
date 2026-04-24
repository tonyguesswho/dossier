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

from pydantic import BaseModel, ConfigDict

from dossier.core.db import get_engine
from dossier.core.llm import CHEAP_MODEL_ID, strong_model
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


class _ExtractedCompany(BaseModel):
    """Structured output for deck → company-name extraction."""
    model_config = ConfigDict(extra="forbid")
    company_name: str
    confidence: float  # 0.0–1.0; below ~0.5 means "not confident"


_COMPANY_EXTRACT_SYSTEM = """You identify the subject company of a pitch deck.

Given the first pages of a pitch deck's extracted text, return the company
name that owns the deck. This is almost always on the cover slide or in
the first heading.

Rules:
- Return the SHORT brand name (e.g. "Uber", "Airbnb", "Stripe"), not a long
  tagline or product descriptor.
- If the deck is clearly about multiple companies (a market-research deck, a
  portfolio review), pick the single most prominent subject.
- If you can't confidently identify a subject, set confidence below 0.5 and
  put your best guess (or "unknown") in company_name.
"""


def extract_company_from_markdown(markdown: str, *, client=None) -> str | None:
    """Return the subject company name, or None if extraction fails / is unsure.

    Uses Haiku for cost (cheap_model_id) via OpenRouter + structured output.
    Truncates to the first 3000 chars — the cover and executive-summary slides
    are where the brand name lives; beyond that it's mostly body content that
    just adds cost without signal.

    Fail-open: any LLM error (network, parse, empty) returns None so the
    caller falls back to filename-derived display name. Deck upload path is
    already wrapped in a broad try/except; this function must not raise.
    """
    if not markdown or not markdown.strip():
        return None

    sample = markdown[:3000]
    active_client = client if client is not None else strong_model()
    try:
        completion = active_client.beta.chat.completions.parse(
            model=CHEAP_MODEL_ID,
            messages=[
                {"role": "system", "content": _COMPANY_EXTRACT_SYSTEM},
                {"role": "user", "content": sample},
            ],
            response_format=_ExtractedCompany,
        )
        parsed = completion.choices[0].message.parsed
    except Exception:
        logger.warning("deck: company-name extraction failed", exc_info=True)
        return None

    if parsed is None or parsed.confidence < 0.5 or not parsed.company_name.strip():
        return None
    name = parsed.company_name.strip()
    if name.lower() in {"unknown", "n/a", "none"}:
        return None
    return name


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
