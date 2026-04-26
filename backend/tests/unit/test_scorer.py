"""Tests for citation_precision() — pure function, synthetic fixtures."""
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
    return Claim(
        section=section,
        claim_text=f"claim about {quoted_span[:20]}",
        quoted_span=quoted_span,
        source_chunk_id=source_chunk_id,
    )


def test_exact_match_scores_one() -> None:
    claims = [_make_claim(quoted_span="founded in 2024 by two Stanford grads")]
    corpus = {"chunk-1": "The company was founded in 2024 by two Stanford grads."}
    assert citation_precision(claims, corpus) == 1.0


def test_normalized_match_scores_one() -> None:
    """Case + whitespace differ but normalization matches."""
    claims = [_make_claim(quoted_span="Founded  In\t2024  By Two  Stanford Grads")]
    corpus = {"chunk-1": "the company was founded in 2024 by two stanford grads."}
    assert citation_precision(claims, corpus) == 1.0


def test_phantom_quote_scores_zero() -> None:
    """Quote does NOT appear in the cited chunk."""
    claims = [_make_claim(quoted_span="reached 50k MAU in Q1 2025")]
    corpus = {"chunk-1": "The company was founded in 2024 by two Stanford grads."}
    assert citation_precision(claims, corpus) == 0.0


def test_empty_claims_returns_one_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Empty claims is vacuously perfect, but must log a warning so CI can flag it."""
    with caplog.at_level(logging.WARNING):
        score = citation_precision([], {"chunk-1": "anything"})
    assert score == 1.0
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_unknown_source_chunk_id_scores_zero() -> None:
    claims = [_make_claim(quoted_span="anything", source_chunk_id="chunk-missing")]
    corpus = {"chunk-1": "irrelevant source text"}
    assert citation_precision(claims, corpus) == 0.0


def test_mixed_hits_and_misses_returns_fraction() -> None:
    claims = [
        _make_claim(quoted_span="founded in 2024", source_chunk_id="chunk-1"),
        _make_claim(quoted_span="reached 50k MAU", source_chunk_id="chunk-1"),
    ]
    corpus = {"chunk-1": "The company was founded in 2024 by two Stanford grads."}
    assert citation_precision(claims, corpus) == 0.5
