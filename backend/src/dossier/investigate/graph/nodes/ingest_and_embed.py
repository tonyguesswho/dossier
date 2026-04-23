"""Ingest & embed node — chunks, embeds, persists source chunks; runs injection classifier.

Injection classifier (GUARD-02 / D-06): Haiku 4.5 judge per chunk; quarantines
flagged chunks to injection_attempts table (D-07). Plan 03-08 fills the classifier.
"""
from __future__ import annotations

import logging

from ..state import DossierState

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    """Chunk + embed + persist tool results. Injection classifier stub."""
    logger.info(
        "ingest_and_embed: processing %d retrieved_chunks",
        len(state.get("retrieved_chunks", [])),
    )
    # Full implementation in Plans 03-03 (DB pool) and 03-08 (classifier)
    raise NotImplementedError("ingest_and_embed: implemented across Plans 03-03 and 03-08")
