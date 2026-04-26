"""Pitch-deck ingestion — PDF → markdown → graph.

MarkItDown handles PDF parsing via pdfminer.six. Output for stylized text-box
decks can be jumbled, but for whitepapers and reports it's clean.

Pre-ingest the uploaded markdown as one synthetic ToolResult before invoking
the graph. The graph reads input_type='deck' from the investigations row and
short-circuits stage1/stage2 fan-out, so no web tools fire and the deck stays
the only corpus.

Dispatch is local-only — Lambda self-invoke for decks would need S3 to ferry
the markdown (256 KB Event payload limit can't carry it).
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
    """Convert PDF bytes to markdown via MarkItDown."""
    from markitdown import MarkItDown  # noqa: PLC0415

    md = MarkItDown()
    stream = io.BytesIO(pdf_bytes)
    result = md.convert_stream(stream, file_extension=".pdf")
    return result.text_content or ""


class _ExtractedCompany(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str
    confidence: float  # 0.0–1.0


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
    """Subject-company extraction via Haiku. Returns None on uncertainty.

    Truncates to the first 3000 chars — the cover and exec-summary slides
    carry the brand name; further pages are body content that adds cost
    without signal. Must never raise (caller falls back to filename).
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
        logger.warning("deck: company extraction failed", exc_info=True)
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
    """Pre-ingest the deck as a synthetic ToolResult, then run the graph.

    On pre-ingest failure we mark the investigation failed and return — the
    graph is never run against a half-populated chunk table.
    """
    import asyncio  # noqa: PLC0415
    from dossier.investigate.graph.runner import run_graph  # noqa: PLC0415

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

    asyncio.run(run_graph(str(investigation_id)))


__all__ = ["extract_company_from_markdown", "pdf_to_markdown", "run_deck_investigation"]
