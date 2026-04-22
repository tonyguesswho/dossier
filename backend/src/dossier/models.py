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


class BriefClaim(BaseModel):
    """One claim emitted by the synthesizer, pre-grounding.

    The synthesizer returns (claim_text, quoted_span, source_chunk_id) per CONTEXT.md D-03.
    Phase 2's ground.py resolves these to full Claim rows with grounded_span_* fields
    via normalized-substring match against source_chunks.text. Claims that cannot be
    grounded are still written with grounded_source_chunk_id=NULL per D-06 so Phase 4's
    hallucination_rate metric can count them.

    Rejected alternatives:
      - Inline [S:chunk_id] citation markers in brief_markdown (ARCHITECTURE.md §4):
        defers to Phase 4 — Phase 2 uses structured Pydantic claim rows instead.
      - source_chunk_id as UUID: keep as str to match Claim (models.py line 49) —
        synthetic tests and real DB callers both use this type.
    """

    model_config = ConfigDict(from_attributes=True)

    claim_text: str
    quoted_span: str
    source_chunk_id: str


class Brief(BaseModel):
    """A full 6-section brief as emitted by the synthesizer (CONTEXT.md D-03 / BRIEF-01).

    Passes through `openai.beta.chat.completions.parse` per D-03/D-05 — structured
    output validated at the API boundary, not regex-parsed. Malformed JSON from
    OpenRouter fails fast with a clear error in investigations.error rather than
    limping forward with a half-parsed brief.

    Field names are template slots (not DB section values). They align with BRIEF-01's
    six fixed sections: Founders, Company, Market, Product, Risk Flags, Suggested Questions.
    Note the plural `risk_flags` here vs. the singular `risk` BriefSection literal — the
    Literal names DB-column section values (used by Claim.section); the Brief field names
    are synthesizer-facing slot names.

    Phase 2 scope notes:
      - BRIEF-04 (risk flags as citable specific concerns) tightens in Phase 4.
      - BRIEF-03 (suggested_questions auto-gen from risk flags + retrieval gaps) tightens
        in Phase 4. Phase 2 emits best-effort suggested_questions only.
      - BRIEF-05 (confidence badges) deferred to Phase 4 — no `confidence: float` field here.
    """

    model_config = ConfigDict(from_attributes=True)

    founders: list[BriefClaim]
    company: list[BriefClaim]
    market: list[BriefClaim]
    product: list[BriefClaim]
    risk_flags: list[BriefClaim]
    suggested_questions: list[BriefClaim]


__all__ = ["BriefSection", "BriefClaim", "Brief", "Claim", "GoldClaim"]
