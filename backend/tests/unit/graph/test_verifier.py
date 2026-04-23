"""Unit tests for verifier.route() reflection cap (WARNING-2 / D-02 / Pitfall 3.1)."""
import pytest


def _make_state(**overrides):
    """Build a minimal DossierState-shaped dict for verifier tests."""
    base = {
        "investigation_id": "test-id",
        "company": "TestCo",
        "context_hint": None,
        "input_url": None,
        "reflection_count": 0,
        "should_regather": False,
        "targeted_sections": [],
        "founder_candidates": [],
        "retrieved_chunks": [],
        "draft_claims": [],
        "grounded_claims": [],
    }
    base.update(overrides)
    return base


def test_reflection_cap_at_2_routes_to_synthesizer():
    """reflection_count=2 + should_regather=True → must route to synthesizer (cap enforced)."""
    from dossier.investigate.graph.nodes.verifier import route

    state = _make_state(reflection_count=2, should_regather=True)
    assert route(state) == "synthesizer", (
        "verifier.route must return 'synthesizer' when reflection_count >= 2, "
        "even if should_regather=True (Pitfall 3.1 — infinite reflection prevention)"
    )


def test_reflection_below_cap_routes_to_gather():
    """reflection_count=1 + should_regather=True → must route back to gather_fanout."""
    from dossier.investigate.graph.nodes.verifier import route

    state = _make_state(reflection_count=1, should_regather=True)
    assert route(state) == "gather_fanout", (
        "verifier.route must return 'gather_fanout' when should_regather=True "
        "and reflection_count < 2"
    )


def test_no_regather_routes_to_synthesizer():
    """should_regather=False → route to synthesizer regardless of reflection count."""
    from dossier.investigate.graph.nodes.verifier import route

    state = _make_state(reflection_count=0, should_regather=False)
    assert route(state) == "synthesizer"
