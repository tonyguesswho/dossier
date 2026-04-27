from __future__ import annotations
# append_* → additive list reducer; set_* → scalar replace. Names are the contract.

from .state import DraftClaimRef, GroundedClaimRef, RetrievedChunkRef


def append_founder_candidates(names: list[str]) -> dict:
    return {"founder_candidates": names}


def append_retrieved_chunks(refs: list[RetrievedChunkRef]) -> dict:
    return {"retrieved_chunks": refs}


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


__all__ = [
    "append_founder_candidates",
    "append_retrieved_chunks",
    "append_draft_claims",
    "append_grounded_claims",
    "set_reflection",
]
