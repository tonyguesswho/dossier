"""Synthesizer node — reuses Phase 2 synthesize.py + retrieve.py unchanged.

Flow:
  1. Retrieve top-k=20 chunks from pgvector (wider than Phase 2's per-section
     k=6 × 6 sections = 36 candidates — a single k=20 query gives the LLM a
     broad context window without exploding token count).
  2. Call synthesize_brief(retrieved, company, context_hint) for a Brief model.
     synthesize.py ALREADY implements the GUARD-01 retrieved_content delimiter
     sandbox (CONTEXT.md D-26 / D-06 in synthesize.py) and the Phase 2 source-
     diversity instruction — this node inherits both without any additional
     wiring.
  3. Flatten Brief.founders/company/market/product/risk_flags/suggested_questions
     into DraftClaimRef list in state. Verifier (Plan 03-02) reads draft_claims
     to check per-section coverage (D-02 reflection trigger).
  4. UPDATE investigations.status='synthesizing' via async session.

Why asyncio.to_thread:
  Phase 2 retrieve_top_k and synthesize_brief are sync (embeddings over the
  sync openai client, sync sqlalchemy engine). Wrapping in to_thread preserves
  the audited Phase 2 logic bit-identical so the Phase 4 eval harness replay
  does not see behavioural drift, and keeps this coroutine non-blocking.

Why DB section names in DraftClaimRef.section (not Brief field names):
  state.py declares section as "'founders'|'company'|'market'|'product'|'risk'
  |'suggested_questions'" — the DB-facing BriefSection Literal values. Brief
  uses the synthesizer-facing field names (plural risk_flags), so we translate
  via the SECTION_FIELD_TO_DB mapping imported from ground.py. This keeps the
  verifier's section-coverage check (Plan 03-02) aligned with the Literal in
  state.py.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import text

from ..state import DossierState, DraftClaimRef

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    """Retrieve top-k → synthesize brief → flatten to DraftClaimRef list.

    Returns {"draft_claims": [...]} — verifier inspects this for per-section
    zero-claim detection (D-02 reflection trigger).
    """
    # Deferred imports keep this module importable in environments that lack
    # the Phase 2 deps (pgvector client, openai SDK). Matches founder_extraction.
    from dossier.core.db import get_async_session
    from dossier.investigate.ground import SECTION_FIELD_TO_DB
    from dossier.investigate.retrieve import retrieve_top_k
    from dossier.investigate.synthesize import synthesize_brief

    investigation_id_str = state["investigation_id"]
    investigation_uuid = UUID(investigation_id_str)
    company = state["company"]
    context_hint = state.get("context_hint")

    logger.info(
        "synthesizer: retrieving context + synthesizing brief for investigation_id=%s",
        investigation_id_str,
    )

    # ---- Retrieve top-k chunks from pgvector (sync → to_thread) ------------
    # Top-k=20 gives the synthesizer broad context across sources; lower k
    # starves the thin-section protection (INVEST-06) and pushes the verifier
    # straight into a re-gather loop.
    retrieved = await asyncio.to_thread(
        retrieve_top_k, investigation_uuid, company, k=20
    )

    if not retrieved:
        logger.warning(
            "synthesizer: retrieve_top_k returned 0 chunks for investigation_id=%s — "
            "synthesizer will emit empty sections and verifier will trigger re-gather",
            investigation_id_str,
        )

    # ---- Call Phase 2 synthesize_brief (sync → to_thread) ------------------
    # Inherits synthesize.py's GUARD-01 delimiter sandbox and source-diversity
    # system prompt unchanged. synthesize_brief raises PipelineError on model
    # refusal or parsed=None — we let that propagate up to runner.py which
    # records investigations.status='failed' (fail-fast per CONTEXT.md D-05).
    brief = await asyncio.to_thread(
        synthesize_brief, retrieved, company, context_hint
    )

    # ---- UPDATE investigations.status='synthesizing' -----------------------
    # Single-statement transaction; cheap and the verifier needs the status
    # transition for the Phase 2 polling-contract compatibility (INVEST-03).
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text(
                    "UPDATE investigations "
                    "SET status = CAST(:s AS investigation_status) "
                    "WHERE id = CAST(:id AS UUID)"
                ),
                {"s": "synthesizing", "id": investigation_id_str},
            )

    # ---- Flatten Brief to DraftClaimRef list -------------------------------
    # Brief has 6 named attributes (field names, plural risk_flags); DraftClaimRef
    # carries DB section values (singular risk). SECTION_FIELD_TO_DB is the
    # canonical mapping shared with Phase 2's ground.py.
    draft_claims: list[DraftClaimRef] = []
    for field_name, db_section in SECTION_FIELD_TO_DB.items():
        section_claims = getattr(brief, field_name, []) or []
        for claim in section_claims:
            draft_claims.append(
                DraftClaimRef(
                    section=db_section,
                    claim_text=claim.claim_text,
                    quoted_span=claim.quoted_span,
                    source_chunk_id=claim.source_chunk_id,
                )
            )

    logger.info(
        "synthesizer: produced %d draft claim(s) across 6 sections for investigation_id=%s",
        len(draft_claims),
        investigation_id_str,
    )

    return {"draft_claims": draft_claims}


__all__ = ["run"]
