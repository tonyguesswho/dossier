"""Planner — initialize targeted_sections to all six on first pass."""
from __future__ import annotations

import logging

from ..state import DossierState

logger = logging.getLogger(__name__)
ALL_SECTIONS = ["founders", "company", "market", "product", "risk", "suggested_questions"]


async def run(state: DossierState) -> dict:
    logger.info(
        "planner: investigation_id=%s company=%s",
        state["investigation_id"], state["company"],
    )
    return {
        "targeted_sections": ALL_SECTIONS,
        "reflection_count": 0,
        "should_regather": False,
    }
