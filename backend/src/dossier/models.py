from __future__ import annotations
# Eval-pipeline DB-row contracts. Brief / BriefClaim / BriefSection live in investigate.brief_schema.

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from dossier.investigate.brief_schema import BriefSection


class Claim(BaseModel):
    # source_chunk_id is str (not UUID) so synthetic tests + DB callers share the type.
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
    # source_text is a verbatim snapshot at gold-write time — eval reproducibility doesn't depend on the live page.
    model_config = ConfigDict(from_attributes=True)

    section: BriefSection
    claim_text: str
    quoted_span: str
    source_url: str
    source_text: str


__all__ = ["Claim", "GoldClaim"]
