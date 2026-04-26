"""Tests for ingest.py pure functions — chunking and vector formatting."""
from __future__ import annotations

import pytest

from dossier.investigate.ingest import _chunk_text, _vector_literal


def test_chunk_text_empty_returns_empty() -> None:
    assert _chunk_text("") == []
    assert _chunk_text("   \n\t  ") == []


def test_chunk_text_short_returns_single_chunk() -> None:
    spans = _chunk_text("Short sentence about Acme AI founded in 2024.")
    assert len(spans) == 1
    assert spans[0].chunk_index == 0
    assert spans[0].char_start == 0
    assert spans[0].text == "Short sentence about Acme AI founded in 2024."
    assert spans[0].char_end == len(spans[0].text)


def test_chunk_text_long_produces_ordered_chunks() -> None:
    paragraph = "The company was founded in 2024 by two Stanford grads. " * 400
    spans = _chunk_text(paragraph)
    assert len(spans) >= 2
    for i, s in enumerate(spans):
        assert s.chunk_index == i
    for a, b in zip(spans, spans[1:]):
        assert b.char_start >= a.char_start


def test_chunk_text_char_offsets_extract_chunk_text() -> None:
    text_val = "Acme AI was founded in 2024. It builds tools for VCs."
    spans = _chunk_text(text_val)
    for s in spans:
        assert text_val[s.char_start:s.char_end] == s.text or s.text in text_val


def test_vector_literal_formats_pgvector() -> None:
    vec = [0.1, 0.2, -0.3]
    lit = _vector_literal(vec)
    assert lit.startswith("[")
    assert lit.endswith("]")
    parts = lit[1:-1].split(",")
    assert len(parts) == 3
    assert float(parts[0]) == pytest.approx(0.1, abs=1e-6)
    assert float(parts[2]) == pytest.approx(-0.3, abs=1e-6)
