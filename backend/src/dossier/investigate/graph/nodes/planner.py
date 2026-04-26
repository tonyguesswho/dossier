"""Planner — initialize targeted_sections to all six on first pass."""
from __future__ import annotations

import logging

from dossier.investigate.brief_schema import BRIEF_DB_SECTIONS

from ..state import DossierState
from ..state_accessors import set_reflection

logger = logging.getLogger(__name__)


async def run(state: DossierState) -> dict:
    logger.info(
        "planner: investigation_id=%s company=%s",
        state["investigation_id"], state["company"],
    )
    return set_reflection(
        count=0,
        should_regather=False,
        targeted_sections=list(BRIEF_DB_SECTIONS),
    )
