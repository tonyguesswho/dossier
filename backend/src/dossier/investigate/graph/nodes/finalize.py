from __future__ import annotations

import json
import logging
from collections import defaultdict
from uuid import UUID

from ..state import DossierState, DraftClaimRef, GroundedClaimRef
from ..state_accessors import append_grounded_claims

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    from dossier.core.db import get_async_session
    from dossier.investigate import repository as repo
    from dossier.investigate.brief_schema import (
        DB_TO_FIELD,
        BriefClaim,
        build_brief_from_grouped,
    )
    from dossier.investigate.citation_grounder import ground_and_render

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
    brief_md, stats = await ground_and_render(investigation_uuid, company, brief)

    scorecard_json = json.dumps({
        "citation_precision": stats.precision,
        "grounding_rate": stats.precision,
        "total_claims": stats.claims_written,
        "grounded_claims": stats.claims_grounded,
    })

    grounded: list[GroundedClaimRef] = []
    async with get_async_session() as session:
        async with session.begin():
            await repo.acomplete_investigation(
                session, investigation_id_str, brief_md, scorecard_json=scorecard_json
            )
            result = await repo.alist_grounded_claims(session, investigation_id_str)
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
