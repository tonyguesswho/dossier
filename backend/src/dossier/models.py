"""Pydantic contracts for Dossier.

These are the source-of-truth types used by the scorer, the Phase 2 RAG pipeline,
the Phase 4 eval harness, and the Phase 6 chat API. A change here propagates
through every downstream phase — treat this file as a public interface.

Contracts:
  - Claim          : one row in the claims table (ARCHITECTURE.md §5).
  - GoldClaim      : one entry in a gold brief JSON (per CONTEXT.md D-02).
  - BriefSection   : literal of the 6 brief sections.
"""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# The 6 brief sections, locked by CONTEXT.md D-02.
# Keep this in sync with the gold brief JSON schema and the Phase 4 section ordering.
BriefSection = Literal[
    "founders",
    "company",
    "market",
    "product",
    "risk",
    "suggested_questions",
]


class Claim(BaseModel):
    """A claim as emitted by the synthesizer or stored in the claims table.

    Maps to the claims table in ARCHITECTURE.md §5 with two additions:
      - `quoted_span`: the verbatim substring (not a column in the SQL schema because
        it is reconstructable from source_chunks.text[grounded_span_start:grounded_span_end],
        but the scorer needs it inline to avoid DB round-trips — D-06 requires a pure function).
      - `source_chunk_id` is `str` (not UUID) so synthetic tests and real DB callers can both
        use this type; the caller stringifies the DB UUID.
    """

    model_config = ConfigDict(from_attributes=True)

    id: Optional[UUID] = None
    investigation_id: Optional[UUID] = None
    section: str
    claim_text: str
    quoted_span: str
    source_chunk_id: str
    grounded_span_start: Optional[int] = None
    grounded_span_end: Optional[int] = None
    confidence: Optional[float] = None
    ordinal: Optional[int] = None


class GoldClaim(BaseModel):
    """A single hand-authored claim in a gold brief JSON file.

    Defined by CONTEXT.md D-02. Stored at rest as `eval_items.gold_brief_json: list[GoldClaim]`.
    `source_text` is a verbatim snapshot at gold-write time (D-04) — this decouples eval
    reproducibility from web page drift.
    """

    model_config = ConfigDict(from_attributes=True)

    section: BriefSection
    claim_text: str
    quoted_span: str
    source_url: str
    source_text: str


__all__ = ["BriefSection", "Claim", "GoldClaim"]
