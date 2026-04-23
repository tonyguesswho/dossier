"""Planner node — sets targeted_sections=[all] on first pass; uses verifier sections on reflection.

Per D-02 / 03-CONTEXT.md §Claude's Discretion: pure Python (no LLM call) for
the first-pass case; only needs to initialize targeted_sections.
"""
from __future__ import annotations

import logging

from ..state import DossierState

logger = logging.getLogger(__name__)
ALL_SECTIONS = ["founders", "company", "market", "product", "risk", "suggested_questions"]


async def run(state: DossierState) -> dict:
    logger.info(
        "planner: investigation_id=%s company=%s",
        state["investigation_id"],
        state["company"],
    )
    return {
        "targeted_sections": ALL_SECTIONS,
        "reflection_count": 0,
        "should_regather": False,
    }
