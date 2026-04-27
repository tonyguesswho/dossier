from __future__ import annotations
# Reflection cap of 2 — without it the loop runs forever on unanswerable sections.

import logging

from dossier.investigate.brief_schema import BRIEF_DB_SECTIONS

from ..state import DossierState
from ..state_accessors import set_reflection

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    current_count = state.get("reflection_count", 0)
    if current_count >= 2:
        logger.info("verifier: reflection cap reached — proceeding to synthesizer")
        return set_reflection(
            count=current_count, should_regather=False, targeted_sections=[]
        )

    claimed_sections = {c.section for c in state.get("draft_claims", [])}
    empty_sections = [s for s in BRIEF_DB_SECTIONS if s not in claimed_sections]

    if empty_sections:
        logger.info("verifier: empty sections=%s — re-gathering", empty_sections)
        return set_reflection(
            count=current_count + 1,
            should_regather=True,
            targeted_sections=empty_sections,
        )

    logger.info("verifier: all sections covered — proceeding to synthesizer")
    return set_reflection(
        count=current_count, should_regather=False, targeted_sections=[]
    )


def route(state: DossierState) -> str:
    # Cap takes priority over regather flag.
    if state.get("should_regather") and state.get("reflection_count", 0) < 2:
        return "gather_fanout"
    return "synthesizer"
