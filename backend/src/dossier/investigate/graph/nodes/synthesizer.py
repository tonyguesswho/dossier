"""Synthesizer — top-k retrieve, call synthesize_brief, flatten to draft claims.

Reuses the synchronous Phase-2 retrieve_top_k + synthesize_brief unchanged
(via asyncio.to_thread). synthesize.py already implements the retrieved-content
delimiter sandbox, so this node inherits injection-defense without extra wiring.

Brief uses field names ('risk_flags'); state.section uses DB Literal values
('risk'). FIELD_TO_DB from brief_schema does the translation.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy import text

from ..state import DossierState, DraftClaimRef
from ..state_accessors import append_draft_claims

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    """Returns {"draft_claims": [...]} — verifier inspects per-section coverage."""
    from dossier.core.db import get_async_session
    from dossier.investigate.brief_schema import FIELD_TO_DB
    from dossier.investigate.retrieve import retrieve_top_k
    from dossier.investigate.synthesize import synthesize_brief

    investigation_id_str = state["investigation_id"]
    investigation_uuid = UUID(investigation_id_str)
    company = state["company"]
    context_hint = state.get("context_hint")

    logger.info(
        "synthesizer: retrieving + synthesizing for investigation_id=%s",
        investigation_id_str,
    )

    # k=20 gives the synthesizer broad context across sources without blowing
    # the token budget. Lower k starves the per-section coverage check.
    retrieved = await asyncio.to_thread(
        retrieve_top_k, investigation_uuid, company, k=20
    )

    if not retrieved:
        logger.warning(
            "synthesizer: 0 chunks retrieved — sections will be empty, verifier will re-gather",
        )

    # synthesize_brief raises on refusal/parse failure; runner.py catches and
    # marks the investigation failed (we want fail-fast on synthesis).
    brief = await asyncio.to_thread(
        synthesize_brief, retrieved, company, context_hint
    )

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

    draft_claims: list[DraftClaimRef] = []
    for field_name, db_section in FIELD_TO_DB.items():
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
        "synthesizer: produced %d draft claim(s) for investigation_id=%s",
        len(draft_claims), investigation_id_str,
    )

    return append_draft_claims(draft_claims)


__all__ = ["run"]
