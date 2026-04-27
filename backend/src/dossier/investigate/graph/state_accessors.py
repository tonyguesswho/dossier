from __future__ import annotations

from .state import DraftClaimRef, GroundedClaimRef, StagedSourceRef


def append_founder_candidates(names: list[str]) -> dict:
    return {"founder_candidates": names}


def append_staged_sources(refs: list[StagedSourceRef]) -> dict:
    return {"staged_sources": refs}


def append_draft_claims(claims: list[DraftClaimRef]) -> dict:
    return {"draft_claims": claims}


def append_grounded_claims(claims: list[GroundedClaimRef]) -> dict:
    return {"grounded_claims": claims}


def set_reflection(
    *,
    count: int,
    should_regather: bool,
    targeted_sections: list[str],
) -> dict:
    # All three move in lockstep — planner and verifier both update them together.
    return {
        "reflection_count": count,
        "should_regather": should_regather,
        "targeted_sections": targeted_sections,
    }

