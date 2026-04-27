from __future__ import annotations
# Annotated[list[X], add] makes parallel Send branches concatenate safely.
# Nodes mutate via state_accessors.{append_*, set_*}, never raw dicts.
# Raw source text MUST NOT enter state — chunk refs only; text lives in source_chunks/S3.

from operator import add
from typing import Annotated, TypedDict

from pydantic import BaseModel


class FounderCandidate(BaseModel):
    name: str
    confidence: str  # 'high' | 'medium' | 'low'


class FounderCandidates(BaseModel):
    founders: list[FounderCandidate]


class RetrievedChunkRef(BaseModel):
    chunk_id: str
    source_id: str
    url: str
    source_kind: str  # 'web'|'github'|'news'|'crawl'|'crunchbase'
    char_start: int
    char_end: int
    section_hint: str


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
    retrieved_chunks: Annotated[list[RetrievedChunkRef], add]
    draft_claims: Annotated[list[DraftClaimRef], add]
    grounded_claims: Annotated[list[GroundedClaimRef], add]


__all__ = [
    "DossierState",
    "FounderCandidates",
    "FounderCandidate",
    "RetrievedChunkRef",
    "DraftClaimRef",
    "GroundedClaimRef",
]
