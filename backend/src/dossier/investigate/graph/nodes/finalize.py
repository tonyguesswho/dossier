"""Finalize node — reuses Phase 2 ground.py; marks status=complete; emits grounded_claims.

Flow:
  1. Reconstruct a Brief model from state.draft_claims (group by section and
     translate db-section values back to Brief field names). ground.py takes a
     Brief so we can reuse it unchanged.
  2. Retrieve top-k=50 chunks from pgvector — wider than the synthesizer's
     k=20 because grounding needs more candidate chunks than synthesis:
     grounding is a substring lookup, recall matters more than token budget.
  3. Call ground_claims(investigation_id, brief, retrieved) via asyncio.to_thread.
     ground.py's substring-match logic runs unchanged (D-07 contract lock);
     claims rows are INSERTed with grounded_source_chunk_id set when matched,
     NULL when unmatched (Phase 4 hallucination_rate counts the NULLs).
  4. UPDATE investigations.status='complete' + set completed_at=now().
  5. WARNING-3 fix: SELECT claims rows with grounded_source_chunk_id IS NOT NULL
     for this investigation and return them as GroundedClaimRef list in state.
     Steps 4 + 5 share a single session.begin() transaction so the status flip
     and the readback see a consistent snapshot of the claims table.

Why reconstruct a Brief from draft_claims rather than thread the Brief through state:
  ARCHITECTURE.md §9 anti-pattern: graph state must not carry heavy LLM output
  objects (they bloat Postgres checkpoint rows). draft_claims is a flat list
  of small refs; reconstructing the Brief shape takes ~6 lines and keeps state
  minimal. Phase 2 ground.py already takes Brief + retrieved, so the wire
  format stays unchanged.

Why top-k=50 for grounding vs k=20 for synthesis:
  Synthesis needs tokens the LLM can actually read (k=20 × ~800 tokens ≈ 16k).
  Grounding is mechanical substring match — no token cost, so a wider net
  improves recall for the INVEST-05 verifier's "drop ungrounded claims" rule.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from uuid import UUID

from sqlalchemy import text

from ..state import DossierState, DraftClaimRef, GroundedClaimRef

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    """Ground draft_claims → mark investigation complete → return grounded_claims."""
    # Deferred imports keep this module importable where Phase 2 deps are absent.
    from dossier.core.db import get_async_session
    from dossier.investigate.ground import SECTION_FIELD_TO_DB, ground_claims
    from dossier.investigate.retrieve import retrieve_top_k
    from dossier.models import Brief, BriefClaim

    investigation_id_str = state["investigation_id"]
    investigation_uuid = UUID(investigation_id_str)
    company = state["company"]
    draft_claims: list[DraftClaimRef] = state.get("draft_claims", []) or []

    logger.info(
        "finalize: grounding %d draft claim(s) for investigation_id=%s",
        len(draft_claims),
        investigation_id_str,
    )

    # ---- Reconstruct Brief from draft_claims ------------------------------
    # Group by DB section; translate DB section → Brief field name via inverse
    # of SECTION_FIELD_TO_DB (DB 'risk' → Brief 'risk_flags').
    db_to_field = {db: field for field, db in SECTION_FIELD_TO_DB.items()}
    grouped: dict[str, list[BriefClaim]] = defaultdict(list)
    for dc in draft_claims:
        field_name = db_to_field.get(dc.section)
        if field_name is None:
            logger.warning(
                "finalize: unknown draft_claim section=%r — skipping", dc.section
            )
            continue
        grouped[field_name].append(
            BriefClaim(
                claim_text=dc.claim_text,
                quoted_span=dc.quoted_span,
                source_chunk_id=dc.source_chunk_id,
            )
        )

    # Brief requires every section field present (no defaults per D-05 fail-fast).
    brief = Brief(
        founders=grouped.get("founders", []),
        company=grouped.get("company", []),
        market=grouped.get("market", []),
        product=grouped.get("product", []),
        risk_flags=grouped.get("risk_flags", []),
        suggested_questions=grouped.get("suggested_questions", []),
    )

    # ---- Retrieve grounding candidates (wider k than synthesis) ------------
    retrieved = await asyncio.to_thread(
        retrieve_top_k, investigation_uuid, company, k=50
    )

    # ---- Call Phase 2 ground_claims — substring-matches + INSERTs rows ------
    # Writes to the claims table in its own sync engine.begin() transaction;
    # asyncio.to_thread keeps this coroutine non-blocking.
    await asyncio.to_thread(
        ground_claims, investigation_uuid, brief, retrieved
    )

    # ---- Mark complete + read back grounded claims -------------------------
    # One session.begin() so the UPDATE status and the SELECT grounded rows
    # observe a consistent snapshot. WARNING-3: populate grounded_claims in
    # state by querying rows with non-null grounded_source_chunk_id.
    grounded: list[GroundedClaimRef] = []
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    UPDATE investigations
                    SET status = CAST(:s AS investigation_status),
                        completed_at = now()
                    WHERE id = CAST(:id AS UUID)
                    """
                ),
                {"s": "complete", "id": investigation_id_str},
            )

            result = await session.execute(
                text(
                    """
                    SELECT
                        id,
                        section,
                        claim_text,
                        grounded_source_chunk_id,
                        grounded_span_start,
                        grounded_span_end
                    FROM claims
                    WHERE investigation_id = CAST(:iid AS UUID)
                      AND grounded_source_chunk_id IS NOT NULL
                    ORDER BY section, ordinal
                    """
                ),
                {"iid": investigation_id_str},
            )
            for row in result:
                grounded.append(
                    GroundedClaimRef(
                        claim_id=str(row.id),
                        section=row.section,
                        claim_text=row.claim_text,
                        grounded_source_chunk_id=str(row.grounded_source_chunk_id),
                        grounded_span_start=row.grounded_span_start,
                        grounded_span_end=row.grounded_span_end,
                    )
                )

    logger.info(
        "finalize: investigation_id=%s complete — %d grounded claim(s) in DB",
        investigation_id_str,
        len(grounded),
    )

    return {"grounded_claims": grounded}


__all__ = ["run"]
