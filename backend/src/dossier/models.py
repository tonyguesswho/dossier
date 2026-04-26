"""DB-row Pydantic contracts for the eval pipeline (Claim, GoldClaim).

The Brief domain — Brief, BriefClaim, BriefSection — lives in
`investigate.brief_schema`. Import from there.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from dossier.investigate.brief_schema import BriefSection


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


__all__ = ["Claim", "GoldClaim"]
