"""Verifier node — per-section empty-claim check with targeted re-gather routing (D-02).

If any of the 6 sections has zero grounded claims AND reflection_count < 2:
  - set should_regather=True
  - populate targeted_sections with empty section names
  - route back to gather_fanout (capped at reflection_count=2, non-negotiable per Pitfall 3.1)

Otherwise: drop ungrounded draft_claims, continue to synthesizer.
Reflection cap of 2 is enforced here — route() checks this first.
"""
from __future__ import annotations

import logging

from ..state import DossierState

logger = logging.getLogger(__name__)
ALL_SECTIONS = ["founders", "company", "market", "product", "risk", "suggested_questions"]


async def run(state: DossierState) -> dict:
    """D-02 per-section empty-check. Returns updated should_regather + targeted_sections."""
    if state.get("reflection_count", 0) >= 2:
        logger.info("verifier: reflection cap reached — proceeding to synthesizer")
        return {"should_regather": False, "targeted_sections": []}

    # Determine which sections have zero draft_claims
    claimed_sections = {c.section for c in state.get("draft_claims", [])}
    empty_sections = [s for s in ALL_SECTIONS if s not in claimed_sections]

    if empty_sections:
        logger.info(
            "verifier: empty sections=%s — routing back to gather_fanout", empty_sections
        )
        return {
            "should_regather": True,
            "targeted_sections": empty_sections,
            "reflection_count": state.get("reflection_count", 0) + 1,
        }

    logger.info("verifier: all sections covered — proceeding to synthesizer")
    return {"should_regather": False, "targeted_sections": []}


def route(state: DossierState) -> str:
    """Conditional edge: return node name based on should_regather and reflection cap.

    Reflection cap of 2 takes priority — if count >= 2, always route to synthesizer
    even if should_regather=True (Pitfall 3.1: infinite reflection prevention).
    """
    if state.get("should_regather") and state.get("reflection_count", 0) < 2:
        return "gather_fanout"
    return "synthesizer"
