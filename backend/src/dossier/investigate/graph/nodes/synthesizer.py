"""Synthesizer node — reuses synthesize.py system prompt and Brief Pydantic model.

Retrieves top-k chunks per section from pgvector, passes to synthesize(),
and populates draft_claims in state. Full implementation in Plan 03-06.
"""
from __future__ import annotations

import logging

from ..state import DossierState

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    """Synthesize brief from retrieved chunks. Full implementation in Plan 03-03+."""
    logger.info("synthesizer: company=%s", state["company"])
    raise NotImplementedError("synthesizer: implemented in Plan 03-03 (needs DB pool for retrieval)")
