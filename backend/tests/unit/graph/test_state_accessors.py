"""Tests for state_accessors — guard the field-name contract with state.py."""
from __future__ import annotations

from typing import get_type_hints

from dossier.investigate.graph.state import (
    DossierState,
    DraftClaimRef,
    GroundedClaimRef,
    StagedSourceRef,
)
from dossier.investigate.graph.state_accessors import (
    append_draft_claims,
    append_founder_candidates,
    append_grounded_claims,
    append_staged_sources,
    set_reflection,
)


def _state_keys() -> set[str]:
    return set(get_type_hints(DossierState).keys())


def test_append_founder_candidates_writes_correct_field() -> None:
    out = append_founder_candidates(["A", "B"])
    assert out == {"founder_candidates": ["A", "B"]}
    assert "founder_candidates" in _state_keys()


def test_append_staged_sources_writes_correct_field() -> None:
    refs: list[StagedSourceRef] = []
    out = append_staged_sources(refs)
    assert out == {"staged_sources": []}
    assert "staged_sources" in _state_keys()


def test_append_draft_claims_writes_correct_field() -> None:
    claims: list[DraftClaimRef] = []
    out = append_draft_claims(claims)
    assert out == {"draft_claims": []}
    assert "draft_claims" in _state_keys()


def test_append_grounded_claims_writes_correct_field() -> None:
    claims: list[GroundedClaimRef] = []
    out = append_grounded_claims(claims)
    assert out == {"grounded_claims": []}
    assert "grounded_claims" in _state_keys()


def test_set_reflection_writes_three_scalars() -> None:
    out = set_reflection(
        count=1, should_regather=True, targeted_sections=["risk"]
    )
    assert out == {
        "reflection_count": 1,
        "should_regather": True,
        "targeted_sections": ["risk"],
    }
    keys = _state_keys()
    assert {"reflection_count", "should_regather", "targeted_sections"} <= keys


def test_accessor_keys_subset_of_state_keys() -> None:
    """Every accessor key must exist on DossierState — guards drift."""
    accessor_keys: set[str] = set()
    for fn, args in (
        (append_founder_candidates, ([],)),
        (append_staged_sources, ([],)),
        (append_draft_claims, ([],)),
        (append_grounded_claims, ([],)),
    ):
        accessor_keys |= fn(*args).keys()
    accessor_keys |= set_reflection(
        count=0, should_regather=False, targeted_sections=[]
    ).keys()
    assert accessor_keys <= _state_keys()
