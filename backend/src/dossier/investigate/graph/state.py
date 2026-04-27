from __future__ import annotations

from datetime import datetime
from operator import add
from typing import Annotated, TypedDict

from pydantic import BaseModel


class FounderCandidate(BaseModel):
    name: str
    confidence: str  # 'high' | 'medium' | 'low'


class FounderCandidates(BaseModel):
    founders: list[FounderCandidate]


class StagedSourceRef(BaseModel):
    url: str
    source_kind: str  # 'web'|'github'|'news'|'crawl'|'crunchbase'
    text: str
    title: str | None = None
    fetched_at: datetime
    raw_metadata: dict[str, object]
    section_hint: str
    pass_index: int


class DraftClaimRef(BaseModel):
    section: str  # 'founders'|'company'|'market'|'product'|'risk'|'suggested_questions'
    claim_text: str
    quoted_span: str
    source_chunk_id: str


class GroundedClaimRef(BaseModel):
    claim_id: str
    section: str
    claim_text: str
    grounded_source_chunk_id: str | None
    grounded_span_start: int | None
    grounded_span_end: int | None


class DossierState(TypedDict):
    investigation_id: str  # thread_id for the checkpointer
    company: str
    context_hint: str | None
    input_url: str | None  # set when input_type='url'; triggers Firecrawl
    input_type: str  # 'name' | 'url' | 'deck'

    # Reflection control — verifier sets regather when a section has zero claims and count<2.
    reflection_count: int
    should_regather: bool
    targeted_sections: list[str]

    # Additive — parallel Send branches concatenate.
    founder_candidates: Annotated[list[str], add]
    staged_sources: Annotated[list[StagedSourceRef], add]
    draft_claims: Annotated[list[DraftClaimRef], add]
    grounded_claims: Annotated[list[GroundedClaimRef], add]

