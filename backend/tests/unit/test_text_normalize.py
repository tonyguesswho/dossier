"""Tests for the canonical citation-matching normalization rule."""
from __future__ import annotations

from dossier.core.text_normalize import normalize


def test_lowercases() -> None:
    assert normalize("Founded In 2024") == "founded in 2024"


def test_collapses_whitespace_runs() -> None:
    assert normalize("founded\t\tin   2024\nby") == "founded in 2024 by"


def test_strips_leading_and_trailing_punctuation() -> None:
    assert normalize('  "founded in 2024."  ') == "founded in 2024"


def test_strips_smart_quotes_and_em_dash() -> None:
    assert normalize("“founded in 2024”—") == "founded in 2024"


def test_keeps_internal_punctuation() -> None:
    assert normalize("a.b") == "a.b"


def test_empty_string() -> None:
    assert normalize("") == ""


def test_grounder_and_scorer_share_the_rule() -> None:
    """Both consumers import from text_normalize, so the rule cannot drift."""
    from dossier.eval import scorer
    from dossier.investigate import ground

    assert scorer.normalize is normalize
    assert ground.normalize is normalize
