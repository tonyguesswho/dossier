from __future__ import annotations

import io
import logging
from uuid import UUID

from sqlalchemy.engine import Engine

from pydantic import BaseModel, ConfigDict

from dossier.core.db import get_engine
from dossier.core.llm import structured_call
from dossier.investigate import repository as repo
from dossier.investigate.ingest import ingest_tool_results
from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)


def pdf_to_markdown(pdf_bytes: bytes, filename: str) -> str:
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
    # Caller falls back to filename on None; must never raise.
    if not markdown or not markdown.strip():
        return None

    # Cover + exec summary carry the brand; further pages add cost without signal.
    sample = markdown[:3000]
    try:
        parsed = structured_call(
            _ExtractedCompany,
            messages=[
                {"role": "system", "content": _COMPANY_EXTRACT_SYSTEM},
                {"role": "user", "content": sample},
            ],
            model="cheap",
            client=client,
        )
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
        repo.mark_failed_with_error(eng, investigation_id, "deck ingest failed")
        return

    from dossier.investigate.graph.runner import run_graph_sync  # noqa: PLC0415
    run_graph_sync(str(investigation_id))


__all__ = ["extract_company_from_markdown", "pdf_to_markdown", "run_deck_investigation"]
