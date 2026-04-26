"""Pydantic contracts shared across synth, ground, eval, and chat.

Public interface — a change here propagates to every downstream consumer.
"""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

BriefSection = Literal[
    "founders",
    "company",
    "market",
    "product",
    "risk",
    "suggested_questions",
]


class Claim(BaseModel):
    """One row of the claims table.

    quoted_span is reconstructable from source_chunks.text[start:end], but
    the scorer needs it inline to avoid DB round-trips. source_chunk_id is
    str (not UUID) so synthetic tests and real DB callers share the type.
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
    """One hand-authored claim in a gold brief JSON.

    source_text is a verbatim snapshot at gold-write time so eval
    reproducibility doesn't depend on the web page still being live.
    """

    model_config = ConfigDict(from_attributes=True)

    section: BriefSection
    claim_text: str
    quoted_span: str
    source_url: str
    source_text: str


class BriefClaim(BaseModel):
    """Synthesizer output (pre-grounding). ground.py resolves it to a full
    Claim row with grounded_span_* via normalized-substring match. Ungrounded
    claims still get persisted with grounded_source_chunk_id=NULL so the
    hallucination metric can count them.
    """

    model_config = ConfigDict(from_attributes=True)

    claim_text: str
    quoted_span: str
    source_chunk_id: str


class Brief(BaseModel):
    """6-section synthesizer output, validated by openai's structured outputs.

    Field names are synthesizer-facing slot names ('risk_flags'); BriefSection
    literals are DB-facing values ('risk'). SECTION_FIELD_TO_DB in ground.py
    is the canonical translation.
    """

    model_config = ConfigDict(from_attributes=True)

    founders: list[BriefClaim]
    company: list[BriefClaim]
    market: list[BriefClaim]
    product: list[BriefClaim]
    risk_flags: list[BriefClaim]
    suggested_questions: list[BriefClaim]


__all__ = ["BriefSection", "BriefClaim", "Brief", "Claim", "GoldClaim"]
