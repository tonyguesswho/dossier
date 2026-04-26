"""Finalize — ground draft claims, render the brief, mark complete.

Reconstructs a Brief from state.draft_claims (cheap, ~6 lines) instead of
threading the full Brief through state — keeps checkpoint rows small.

Uses k=50 for grounding vs k=20 for synthesis: grounding is mechanical
substring matching, recall matters more than token budget. Synthesis was
limited by what the LLM can read.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from uuid import UUID

from sqlalchemy import text

from ..state import DossierState, DraftClaimRef, GroundedClaimRef
from ..state_accessors import append_grounded_claims

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    from dossier.core.db import get_async_session
    from dossier.investigate.brief_schema import (
        DB_TO_FIELD,
        BriefClaim,
        build_brief_from_grouped,
    )
    from dossier.investigate.ground import ground_claims
    from dossier.investigate.retrieve import retrieve_top_k

    investigation_id_str = state["investigation_id"]
    investigation_uuid = UUID(investigation_id_str)
    company = state["company"]
    draft_claims: list[DraftClaimRef] = state.get("draft_claims", []) or []

    logger.info(
        "finalize: grounding %d draft claim(s) for investigation_id=%s",
        len(draft_claims), investigation_id_str,
    )

    grouped: dict[str, list[BriefClaim]] = defaultdict(list)
    for dc in draft_claims:
        field_name = DB_TO_FIELD.get(dc.section)
        if field_name is None:
            logger.warning("finalize: unknown section=%r — skipping", dc.section)
            continue
        grouped[field_name].append(
            BriefClaim(
                claim_text=dc.claim_text,
                quoted_span=dc.quoted_span,
                source_chunk_id=dc.source_chunk_id,
            )
        )

    brief = build_brief_from_grouped(grouped)

    retrieved = await asyncio.to_thread(
        retrieve_top_k, investigation_uuid, company, k=50
    )

    await asyncio.to_thread(
        ground_claims, investigation_uuid, brief, retrieved
    )

    # Build the chunk_id → url map across the whole investigation corpus
    # so claims can be cited even when grounding pinned them to a chunk
    # outside the top-k. Fail-open: if the SELECT errors, render with an
    # empty map and let claims fall back to "(source)" without a link.
    from dossier.investigate.render import brief_to_markdown as _brief_to_markdown  # noqa: PLC0415
    url_by_chunk: dict[str, str] = {}
    try:
        async with get_async_session() as session:
            url_rows_result = await session.execute(
                text(
                    "SELECT sc.id::text AS chunk_id, s.url AS url "
                    "FROM source_chunks sc "
                    "JOIN sources s ON sc.source_id = s.id "
                    "WHERE s.investigation_id = CAST(:iid AS UUID)"
                ),
                {"iid": investigation_id_str},
            )
            if url_rows_result is not None:
                url_by_chunk = {r.chunk_id: r.url for r in url_rows_result}
    except Exception:  # noqa: BLE001 — last-mile render must never poison status
        logger.exception("finalize: url_by_chunk lookup failed; rendering without links")
    brief_md = _brief_to_markdown(brief, url_by_chunk)

    # Single transaction for the status flip + grounded-claim readback so
    # they observe a consistent snapshot of the claims table.
    grounded: list[GroundedClaimRef] = []
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text(
                    """
                    UPDATE investigations
                    SET status = CAST(:s AS investigation_status),
                        completed_at = now(),
                        brief_markdown = :md
                    WHERE id = CAST(:id AS UUID)
                    """
                ),
                {"s": "complete", "md": brief_md, "id": investigation_id_str},
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
        "finalize: investigation_id=%s complete — %d grounded claim(s)",
        investigation_id_str, len(grounded),
    )

    return append_grounded_claims(grounded)


__all__ = ["run"]
