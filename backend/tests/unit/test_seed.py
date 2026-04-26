"""Tests for the eval_items seed script's pure-Python parts (no Postgres)."""
from __future__ import annotations

import json
import pathlib

import pytest

from dossier.eval.companies import EVAL_COMPANIES
from dossier.eval.seed import _GOLDS_DIR, _load_gold_claims


def test_every_company_has_a_seed_url() -> None:
    assert all(c.seed_url.startswith("https://") for c in EVAL_COMPANIES)


def test_holdout_companies_have_no_gold_filename() -> None:
    for c in EVAL_COMPANIES:
        if c.holdout:
            assert c.gold_filename is None, f"{c.company_name}: holdout must be ungolded"


def test_golded_companies_are_not_holdout() -> None:
    for c in EVAL_COMPANIES:
        if c.gold_filename is not None:
            assert not c.holdout, f"{c.company_name}: cannot be both golded and holdout"


def test_every_declared_gold_filename_exists_on_disk() -> None:
    for c in EVAL_COMPANIES:
        if c.gold_filename is None:
            continue
        path = _GOLDS_DIR / c.gold_filename
        assert path.exists(), f"{c.company_name} declares gold_filename={c.gold_filename} but {path} is missing"


def test_stub_claim_text_is_rejected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stub gold file with placeholder 'Replace with...' text must not seed."""
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

    monkeypatch.setattr("dossier.eval.seed._GOLDS_DIR", tmp_path)

    with pytest.raises(ValueError, match="stub"):
        _load_gold_claims("stub-company.json")


def test_empty_claims_array_is_rejected(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty_path = tmp_path / "empty.json"
    empty_path.write_text(json.dumps({"claims": []}))

    monkeypatch.setattr("dossier.eval.seed._GOLDS_DIR", tmp_path)

    with pytest.raises(ValueError, match="no `claims`"):
        _load_gold_claims("empty.json")


def test_missing_gold_file_raises_not_found(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dossier.eval.seed._GOLDS_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        _load_gold_claims("does-not-exist.json")
