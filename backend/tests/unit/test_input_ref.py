"""Tests for the InvestigationInput value type."""
from __future__ import annotations

from dossier.investigate.input_ref import InvestigationInput


def test_parse_value_only() -> None:
    inp = InvestigationInput.parse("Acme AI")
    assert inp.value == "Acme AI"
    assert inp.context_hint is None


def test_parse_value_with_hint() -> None:
    inp = InvestigationInput.parse("Acme AI\n---HINT---\nseries A meeting")
    assert inp.value == "Acme AI"
    assert inp.context_hint == "series A meeting"


def test_parse_handles_none_and_empty() -> None:
    assert InvestigationInput.parse(None) == InvestigationInput("", None)
    assert InvestigationInput.parse("") == InvestigationInput("", None)


def test_serialize_round_trip_with_hint() -> None:
    inp = InvestigationInput(value="Acme AI", context_hint="ctx")
    assert InvestigationInput.parse(inp.serialize()) == inp


def test_serialize_round_trip_no_hint() -> None:
    inp = InvestigationInput(value="https://example.com")
    assert InvestigationInput.parse(inp.serialize()) == inp


def test_with_value_preserves_hint() -> None:
    inp = InvestigationInput(value="old", context_hint="ctx")
    renamed = inp.with_value("new")
    assert renamed.value == "new"
    assert renamed.context_hint == "ctx"


def test_with_value_preserves_no_hint() -> None:
    inp = InvestigationInput(value="old")
    renamed = inp.with_value("new")
    assert renamed.value == "new"
    assert renamed.context_hint is None
