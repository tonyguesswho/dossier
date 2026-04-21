"""Unit tests for the citation_precision() scorer.

These 5 cases are locked by .planning/phases/01-eval-harness-skeleton/01-CONTEXT.md D-10:
  1. Exact match
  2. Normalized match (case + whitespace)
  3. Phantom quote (fails)
  4. Empty claims list (returns 1.0 with warning)
  5. Claim pointing at unknown source_id (scores 0)

D-06: scorer is a pure function — no DB, no LLM, no network.
D-07: normalization = lowercase + collapse \\s+ + strip leading/trailing punctuation.
D-09: empty claims returns 1.0 and logs a warning.

Tests run against synthetic fixtures only — no real DB, no real gold files.
Target runtime: <50ms for the full file (D-10).
"""
from __future__ import annotations

import logging

import pytest

from dossier.eval.scorer import citation_precision
from dossier.models import Claim


def _make_claim(
    quoted_span: str,
    source_chunk_id: str = "chunk-1",
    section: str = "founders",
) -> Claim:
    """Build a minimally-populated Claim for scorer tests."""
    return Claim(
        section=section,
        claim_text=f"claim about {quoted_span[:20]}",
        quoted_span=quoted_span,
        source_chunk_id=source_chunk_id,
    )


# ---------------------------------------------------------------------------
# Case 1: exact match
# ---------------------------------------------------------------------------
def test_exact_match_scores_one() -> None:
    claims = [_make_claim(quoted_span="founded in 2024 by two Stanford grads")]
    corpus = {"chunk-1": "The company was founded in 2024 by two Stanford grads."}
    assert citation_precision(claims, corpus) == 1.0


# ---------------------------------------------------------------------------
# Case 2: normalized match (case + whitespace differ but normalization matches)
# ---------------------------------------------------------------------------
def test_normalized_match_scores_one() -> None:
    claims = [_make_claim(quoted_span="Founded  In\t2024  By Two  Stanford Grads")]
    corpus = {"chunk-1": "the company was founded in 2024 by two stanford grads."}
    assert citation_precision(claims, corpus) == 1.0


# ---------------------------------------------------------------------------
# Case 3: phantom quote — quote does NOT appear in the cited chunk (Pitfall 1.1)
# ---------------------------------------------------------------------------
def test_phantom_quote_scores_zero() -> None:
    claims = [_make_claim(quoted_span="reached 50k MAU in Q1 2025")]
    corpus = {"chunk-1": "The company was founded in 2024 by two Stanford grads."}
    assert citation_precision(claims, corpus) == 0.0


# ---------------------------------------------------------------------------
# Case 4: empty claims list — returns 1.0 AND logs a warning (D-09)
# ---------------------------------------------------------------------------
def test_empty_claims_returns_one_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        score = citation_precision([], {"chunk-1": "anything"})
    assert score == 1.0
    # D-09: must log a warning so CI can flag suspiciously empty briefs.
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------------
# Case 5: claim points at a source_chunk_id not present in corpus — scores 0
# ---------------------------------------------------------------------------
def test_unknown_source_chunk_id_scores_zero() -> None:
    claims = [_make_claim(quoted_span="anything", source_chunk_id="chunk-missing")]
    corpus = {"chunk-1": "irrelevant source text"}
    assert citation_precision(claims, corpus) == 0.0


# ---------------------------------------------------------------------------
# Mixed case: 1 hit + 1 miss → 0.5 (sanity check that the metric is hits/total)
# ---------------------------------------------------------------------------
def test_mixed_hits_and_misses_returns_fraction() -> None:
    claims = [
        _make_claim(quoted_span="founded in 2024", source_chunk_id="chunk-1"),
        _make_claim(quoted_span="reached 50k MAU", source_chunk_id="chunk-1"),
    ]
    corpus = {"chunk-1": "The company was founded in 2024 by two Stanford grads."}
    assert citation_precision(claims, corpus) == 0.5


# ---------------------------------------------------------------------------
# Signature guard: the function must NOT raise on the edge case (D-09 "does not raise")
# ---------------------------------------------------------------------------
def test_empty_claims_does_not_raise() -> None:
    # Should not raise ZeroDivisionError, ValueError, etc.
    citation_precision([], {})
