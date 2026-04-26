"""Typed update helpers for DossierState — encodes append-vs-replace semantics.

LangGraph applies a reducer per field (operator.add for additive lists,
last-write-wins for scalars). Whether a node appends or replaces is decided
by the field's annotation in `state.py` — easy to misread when authoring a
new node.

These helpers name the semantic at the call site:
  - `append_*`  → list field with an `add` reducer; concatenates across
                  parallel Send branches.
  - `set_*`     → scalar / unannotated field; replaces.

A node returns one of these dicts. Composition is dict-merge:
  {**append_retrieved_chunks(a), **append_draft_claims(b)}.
"""
from __future__ import annotations

from .state import DraftClaimRef, GroundedClaimRef, RetrievedChunkRef


def append_founder_candidates(names: list[str]) -> dict:
    """Adds to state['founder_candidates']. Concurrent Send branches concatenate."""
    return {"founder_candidates": names}


def append_retrieved_chunks(refs: list[RetrievedChunkRef]) -> dict:
    """Adds to state['retrieved_chunks']. Concurrent Send branches concatenate."""
    return {"retrieved_chunks": refs}


def append_draft_claims(claims: list[DraftClaimRef]) -> dict:
    """Adds to state['draft_claims']. Concurrent Send branches concatenate."""
    return {"draft_claims": claims}


def append_grounded_claims(claims: list[GroundedClaimRef]) -> dict:
    """Adds to state['grounded_claims']."""
    return {"grounded_claims": claims}


def set_reflection(
    *,
    count: int,
    should_regather: bool,
    targeted_sections: list[str],
) -> dict:
    """Replaces the three reflection-control scalars together. Planner and
    verifier both update these in lockstep; passing them as one call makes
    that lockstep explicit."""
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
