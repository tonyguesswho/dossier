"""Verifier — per-section empty-claim check; routes back to gather_fanout when
any of the six sections has zero claims and reflection_count < 2.

Reflection cap of 2 is non-negotiable — without it the loop can run forever
when a section is genuinely unanswerable from public sources.
"""
from __future__ import annotations

import logging

from ..state import DossierState

logger = logging.getLogger(__name__)
ALL_SECTIONS = ["founders", "company", "market", "product", "risk", "suggested_questions"]


async def run(state: DossierState) -> dict:
    if state.get("reflection_count", 0) >= 2:
        logger.info("verifier: reflection cap reached — proceeding to synthesizer")
        return {"should_regather": False, "targeted_sections": []}

    claimed_sections = {c.section for c in state.get("draft_claims", [])}
    empty_sections = [s for s in ALL_SECTIONS if s not in claimed_sections]

    if empty_sections:
        logger.info("verifier: empty sections=%s — re-gathering", empty_sections)
        return {
            "should_regather": True,
            "targeted_sections": empty_sections,
            "reflection_count": state.get("reflection_count", 0) + 1,
        }

    logger.info("verifier: all sections covered — proceeding to synthesizer")
    return {"should_regather": False, "targeted_sections": []}


def route(state: DossierState) -> str:
    """Conditional edge. Reflection cap takes priority over regather flag."""
    if state.get("should_regather") and state.get("reflection_count", 0) < 2:
        return "gather_fanout"
    return "synthesizer"
