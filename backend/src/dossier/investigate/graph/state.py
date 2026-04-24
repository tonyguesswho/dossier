"""DossierState — shared scratchpad for the investigation LangGraph.

D-01 (03-CONTEXT.md): TypedDict with additive list reducers for fan-out fields.
List fields use Annotated[list[X], operator.add] so parallel Send branches
concatenate without last-write-wins races.

Scalars (investigation_id, company, context_hint, reflection_count,
should_regather, targeted_sections) replace on write — standard TypedDict
update semantics.

CRITICAL: Do NOT put raw source text in state. S3 keys and chunk IDs travel
in state; text lives in S3 and Postgres (ARCHITECTURE.md §9 anti-pattern).
"""
from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict

from pydantic import BaseModel


class FounderCandidate(BaseModel):
    """Structured output from D-05 Haiku 4.5 founder extraction."""

    name: str
    confidence: str  # 'high' | 'medium' | 'low'


class FounderCandidates(BaseModel):
    """JSON-mode response from founder extraction LLM call (D-05, cap 5)."""

    founders: list[FounderCandidate]


class RetrievedChunkRef(BaseModel):
    """Lightweight chunk reference travelling through graph state.

    text is NOT included — only ids and offsets.
    Full text lives in source_chunks table and S3.
    """

    chunk_id: str  # source_chunks.id (UUID as str)
    source_id: str  # sources.id (UUID as str)
    url: str
    source_kind: str  # 'web'|'github'|'news'|'crawl'|'crunchbase'
    char_start: int
    char_end: int
    section_hint: str  # which brief section this chunk was retrieved for


class DraftClaimRef(BaseModel):
    """Claim emitted by synthesizer node before grounding."""

    section: str  # 'founders'|'company'|'market'|'product'|'risk'|'suggested_questions'
    claim_text: str
    quoted_span: str  # verbatim text the LLM pulled from retrieved content
    source_chunk_id: str  # which chunk supported this claim


class GroundedClaimRef(BaseModel):
    """Claim after finalize node writes it to the claims table."""

    claim_id: str  # claims.id (UUID as str)
    section: str
    claim_text: str
    grounded_source_chunk_id: str | None
    grounded_span_start: int | None
    grounded_span_end: int | None


class DossierState(TypedDict):
    """Shared scratchpad. Read D-01 in 03-CONTEXT.md before modifying.

    List fields (founder_candidates, retrieved_chunks, draft_claims,
    grounded_claims) use operator.add as reducer — parallel Send branches
    concatenate safely. Scalars replace on write.
    """

    # --- Scalars (set by planner, read by all nodes) ---
    investigation_id: str  # UUID as str; thread_id for checkpointer
    company: str
    context_hint: str | None
    input_url: str | None  # present only for 'url' input_type; triggers Firecrawl in Stage 1
    input_type: str  # 'name' | 'url' | 'deck' — gather_fanout short-circuits on 'deck'

    # --- Reflection control (D-02) ---
    reflection_count: int  # incremented by verifier; capped at 2
    should_regather: bool  # verifier sets True when any section has 0 claims + count < 2
    targeted_sections: list[str]  # verifier populates; gather_fanout uses for biased queries

    # --- Accumulation fields (additive reducers for fan-out) ---
    founder_candidates: Annotated[list[str], add]  # names only; max 5 (D-05)
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
