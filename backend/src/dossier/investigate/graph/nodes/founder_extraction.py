"""Founder-name extraction node — stub for Wave 1 graph compilation.

Plan 03-05 (Wave 2) replaces this with a real Haiku 4.5 + FounderCandidates
structured output call. This stub ensures the correct graph topology is
established in Wave 1:
  Stage-1 nodes → founder_extraction → [stage2_router] → Stage-2 nodes

Without this stub, Stage-1 nodes would route directly to ingest_and_embed,
orphaning founder_extraction and Stage-2 (github_founder, crunchbase_search).
"""
from __future__ import annotations

import logging

from ..state import DossierState

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    """Passthrough stub. Plan 03-05 replaces with Haiku 4.5 extraction."""
    logger.info(
        "founder_extraction: stub passthrough for company=%s (Plan 03-05 fills real extraction)",
        state["company"],
    )
    # Returns empty founder_candidates — stage2_router sends only crunchbase (no GitHub)
    # This is the safe default: do not invent founder names (Pitfall defense per D-05)
    return {"founder_candidates": []}
