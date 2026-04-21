"""Unit tests for the eval_items seed script.

These tests DO NOT hit Postgres — they validate the pure-Python parts:
  1. EVAL_COMPANIES satisfies the revised D-11/D-12/D-14 split (10 total, 4 holdout, 3 golded)
  2. Every gold_filename reference resolves to an actual file on disk
  3. GoldClaim Pydantic validation catches malformed stubs before DB writes
  4. The stub-detection rule (claim_text starts with 'Replace') is active

The actual Postgres upsert is exercised by running `python -m dossier.eval.seed` against
docker-compose Postgres — integration/eval-tier tests land in Phase 4 per D-20.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import ValidationError

from dossier.eval.companies import EVAL_COMPANIES, split_summary
from dossier.eval.seed import _GOLDS_DIR, _load_gold_claims
from dossier.models import GoldClaim


# ---------------------------------------------------------------------------
# Split sanity: CONTEXT.md D-11/D-12/D-14 (revised 2026-04-22) — 10 / 4 / 3
# ---------------------------------------------------------------------------
def test_total_company_count_is_ten() -> None:
    assert split_summary()["total"] == 10


def test_holdout_is_exactly_four() -> None:
    assert split_summary()["holdout"] == 4


def test_golded_is_exactly_three() -> None:
    assert split_summary()["golded"] == 3


def test_train_is_exactly_six() -> None:
    assert split_summary()["train"] == 6


def test_at_least_three_verticals_represented() -> None:
    # D-11 revised: ≥3 verticals
    assert split_summary()["verticals"] >= 3


def test_every_company_has_a_seed_url() -> None:
    assert all(c.seed_url.startswith("https://") for c in EVAL_COMPANIES)


def test_every_company_has_notes() -> None:
    assert all(c.notes for c in EVAL_COMPANIES)


def test_holdout_companies_have_no_gold_filename() -> None:
    for c in EVAL_COMPANIES:
        if c.holdout:
            assert c.gold_filename is None, f"{c.company_name}: holdout must be ungolded"


def test_golded_companies_are_not_holdout() -> None:
    # D-14: gold applies only to train-set companies.
    for c in EVAL_COMPANIES:
        if c.gold_filename is not None:
            assert not c.holdout, f"{c.company_name}: cannot be both golded and holdout"


# ---------------------------------------------------------------------------
# Gold file existence: every gold_filename reference must resolve
# ---------------------------------------------------------------------------
def test_every_declared_gold_filename_exists_on_disk() -> None:
    for c in EVAL_COMPANIES:
        if c.gold_filename is None:
            continue
        path = _GOLDS_DIR / c.gold_filename
        assert path.exists(), f"{c.company_name} declares gold_filename={c.gold_filename} but {path} is missing"


# ---------------------------------------------------------------------------
# GoldClaim Pydantic validation — caller-facing integrity check
# ---------------------------------------------------------------------------
def test_valid_gold_claim_constructs_cleanly() -> None:
    # Sanity check: the D-02 shape works.
    claim = GoldClaim(
        section="founders",
        claim_text="A single factual claim.",
        quoted_span="exact substring from source",
        source_url="https://example.com",
        source_text="The paragraph the quote came from, captured at authoring time.",
    )
    assert claim.section == "founders"


def test_invalid_section_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        GoldClaim(
            section="not-a-real-section",  # type: ignore[arg-type]
            claim_text="x",
            quoted_span="y",
            source_url="https://example.com",
            source_text="z",
        )


def test_missing_source_text_raises_validation_error() -> None:
    # D-04 requires source_text — omitting it is a schema violation.
    with pytest.raises(ValidationError):
        GoldClaim(
            section="founders",
            claim_text="x",
            quoted_span="y",
            source_url="https://example.com",
        )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Stub-detection: _load_gold_claims rejects unauthored placeholder content
# ---------------------------------------------------------------------------
def test_stub_claim_text_is_rejected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stub gold file (with placeholder 'Replace with...' text) must not pass seeding."""
    stub = {
        "claims": [
            {
                "section": "founders",
                "claim_text": "Replace with a single-sentence factual claim.",
                "quoted_span": "Replace with the VERBATIM substring.",
                "source_url": "https://example.com",
                "source_text": "Replace with a paragraph snapshot.",
            }
        ]
    }
    stub_path = tmp_path / "stub-company.json"
    stub_path.write_text(json.dumps(stub))

    # Point _GOLDS_DIR at the tmp dir for this test.
    monkeypatch.setattr("dossier.eval.seed._GOLDS_DIR", tmp_path)

    with pytest.raises(ValueError, match="stub"):
        _load_gold_claims("stub-company.json")


def test_empty_claims_array_is_rejected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gold file with zero claims must not pass seeding."""
    empty_path = tmp_path / "empty.json"
    empty_path.write_text(json.dumps({"claims": []}))

    monkeypatch.setattr("dossier.eval.seed._GOLDS_DIR", tmp_path)

    with pytest.raises(ValueError, match="no `claims`"):
        _load_gold_claims("empty.json")


def test_missing_gold_file_raises_not_found(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dossier.eval.seed._GOLDS_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        _load_gold_claims("does-not-exist.json")
