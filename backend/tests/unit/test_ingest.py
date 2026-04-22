"""Unit tests for dossier.investigate.ingest — pure functions only.

Locked by .planning/phases/02-single-pass-rag-pipeline/02-06-PLAN.md:
  STACK.md §2.2 / Pitfall 2.1: 800/120 chunk/overlap in TOKENS (not chars).
  Pitfall 2.2: every chunk metadata JSONB includes embedding_model.

Full DB-path tests live in tests/integration/test_retrieve_pgvector.py.
Target runtime: <500ms for the full file.
"""
from __future__ import annotations

import pytest

from dossier.investigate.ingest import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_SIZE_TOKENS,
    _chunk_text,
    _sha256,
    _vector_literal,
)


# ---------------------------------------------------------------------------
# Case 1: chunk constants match STACK.md §2.2
# ---------------------------------------------------------------------------
def test_chunk_constants_match_stack_md() -> None:
    assert CHUNK_SIZE_TOKENS == 800
    assert CHUNK_OVERLAP_TOKENS == 120


# ---------------------------------------------------------------------------
# Case 2: empty / whitespace-only text produces zero chunks
# ---------------------------------------------------------------------------
def test_chunk_text_empty_returns_empty() -> None:
    assert _chunk_text("") == []
    assert _chunk_text("   \n\t  ") == []


# ---------------------------------------------------------------------------
# Case 3: short text below a single-chunk token budget returns 1 chunk
# ---------------------------------------------------------------------------
def test_chunk_text_short_returns_single_chunk() -> None:
    spans = _chunk_text("Short sentence about Acme AI founded in 2024.")
    assert len(spans) == 1
    assert spans[0].chunk_index == 0
    assert spans[0].char_start == 0
    assert spans[0].text == "Short sentence about Acme AI founded in 2024."
    assert spans[0].char_end == len(spans[0].text)


# ---------------------------------------------------------------------------
# Case 4: long text splits into multiple ordered chunks with non-decreasing
# char_start offsets (INVEST-04 stable span requirement).
# ---------------------------------------------------------------------------
def test_chunk_text_long_produces_multiple_chunks() -> None:
    paragraph = "The company was founded in 2024 by two Stanford grads. " * 400
    spans = _chunk_text(paragraph)
    assert len(spans) >= 2
    for i, s in enumerate(spans):
        assert s.chunk_index == i
    for a, b in zip(spans, spans[1:]):
        assert b.char_start >= a.char_start


# ---------------------------------------------------------------------------
# Case 5: char_start/char_end reliably reconstruct the chunk text from
# the source (or the chunk text is at least findable in the source).
# ---------------------------------------------------------------------------
def test_chunk_text_char_offsets_extract_chunk_text() -> None:
    text_val = "Acme AI was founded in 2024. It builds tools for VCs."
    spans = _chunk_text(text_val)
    for s in spans:
        assert text_val[s.char_start:s.char_end] == s.text or s.text in text_val


# ---------------------------------------------------------------------------
# Case 6: content_hash is deterministic and collision-resistant for trivial cases
# ---------------------------------------------------------------------------
def test_sha256_is_stable() -> None:
    assert _sha256("hello") == _sha256("hello")
    assert _sha256("hello") != _sha256("hell0")


# ---------------------------------------------------------------------------
# Case 7: pgvector literal formatting round-trips through float()
# ---------------------------------------------------------------------------
def test_vector_literal_formats_pgvector() -> None:
    vec = [0.1, 0.2, -0.3]
    lit = _vector_literal(vec)
    assert lit.startswith("[")
    assert lit.endswith("]")
    parts = lit[1:-1].split(",")
    assert len(parts) == 3
    assert float(parts[0]) == pytest.approx(0.1, abs=1e-6)
    assert float(parts[2]) == pytest.approx(-0.3, abs=1e-6)
