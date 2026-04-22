"""Unit tests for the Brief + BriefClaim Pydantic contracts.

These cases are locked by .planning/phases/02-single-pass-rag-pipeline/02-CONTEXT.md:
  D-03: synthesizer emits one Pydantic Brief via openai.beta.chat.completions.parse
  D-05: malformed JSON fails fast (no graceful partial-brief path)
  BRIEF-01: 6 fixed sections — Founders, Company, Market, Product, Risk Flags, Suggested Questions

Tests run against synthetic dicts only — no real DB, no real OpenRouter call.
Target runtime: <50ms for the full file (PATTERNS.md §"Test docstring citing D-XX").
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from dossier.models import Brief, BriefClaim


def _make_brief_claim(
    claim_text: str = "Founded in 2024 by two Stanford grads",
    quoted_span: str = "founded in 2024 by two Stanford grads",
    source_chunk_id: str = "chunk-1",
) -> dict:
    return {
        "claim_text": claim_text,
        "quoted_span": quoted_span,
        "source_chunk_id": source_chunk_id,
    }


def _make_brief_dict(**section_overrides) -> dict:
    """Build a fully-populated Brief dict. Overrides individual sections as dict kwargs."""
    sections = {
        "founders": [_make_brief_claim()],
        "company": [_make_brief_claim()],
        "market": [_make_brief_claim()],
        "product": [_make_brief_claim()],
        "risk_flags": [_make_brief_claim()],
        "suggested_questions": [_make_brief_claim()],
    }
    sections.update(section_overrides)
    return sections


# ---------------------------------------------------------------------------
# Case 1: round-trip — full 6-section Brief validates cleanly
# ---------------------------------------------------------------------------
def test_brief_round_trip() -> None:
    brief = Brief.model_validate(_make_brief_dict())
    assert len(brief.founders) == 1
    assert brief.founders[0].claim_text == "Founded in 2024 by two Stanford grads"


# ---------------------------------------------------------------------------
# Case 2: BRIEF-01 — all six sections are required (no default [])
# ---------------------------------------------------------------------------
def test_brief_rejects_missing_section() -> None:
    payload = _make_brief_dict()
    payload.pop("risk_flags")
    with pytest.raises(ValidationError, match="risk_flags"):
        Brief.model_validate(payload)


# ---------------------------------------------------------------------------
# Case 3: BriefClaim required fields — D-03 contract
# ---------------------------------------------------------------------------
def test_brief_claim_requires_all_three_fields() -> None:
    with pytest.raises(ValidationError):
        BriefClaim.model_validate({"claim_text": "x", "quoted_span": "y"})  # missing source_chunk_id


# ---------------------------------------------------------------------------
# Case 4: empty section is allowed (an empty list is valid — synthesizer may
#          legitimately return [] for a section; Phase 4 counts it.)
# ---------------------------------------------------------------------------
def test_brief_accepts_empty_section() -> None:
    brief = Brief.model_validate(_make_brief_dict(market=[]))
    assert brief.market == []
