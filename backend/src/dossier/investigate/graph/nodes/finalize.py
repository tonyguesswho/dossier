"""Finalize node — reuses ground.py; persists grounded_claims to DB; marks status=complete.

Calls ground.ground_claims() (unchanged from Phase 2) then updates
investigations.status='complete' and investigations.brief_markdown.
"""
from __future__ import annotations

import logging

from ..state import DossierState

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    """Ground draft_claims and persist to DB. Full implementation in Plan 03-03+."""
    logger.info("finalize: investigation_id=%s", state["investigation_id"])
    raise NotImplementedError("finalize: implemented in Plan 03-03 (needs DB pool)")
