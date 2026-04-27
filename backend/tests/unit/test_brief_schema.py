"""Tests for the brief_schema module — the Brief domain contract."""
from __future__ import annotations

import pytest

from dossier.investigate.brief_schema import (
    BRIEF_DB_SECTIONS,
    BRIEF_FIELDS,
    BRIEF_SECTIONS,
    DB_TO_FIELD,
    FIELD_TO_DB,
    Brief,
    BriefClaim,
    build_brief_from_grouped,
    section_headings,
)


def test_field_db_round_trip() -> None:
    for s in BRIEF_SECTIONS:
        assert DB_TO_FIELD[FIELD_TO_DB[s.field]] == s.field


def test_field_to_db_handles_the_only_renamed_section() -> None:
    assert FIELD_TO_DB["risk_flags"] == "risk"
    assert DB_TO_FIELD["risk"] == "risk_flags"


def test_field_to_db_unknown_raises() -> None:
    with pytest.raises(KeyError):
        _ = FIELD_TO_DB["not-a-real-field"]


def test_brief_fields_match_canonical_order() -> None:
    assert BRIEF_FIELDS == tuple(s.field for s in BRIEF_SECTIONS)
    assert BRIEF_DB_SECTIONS == tuple(s.db_name for s in BRIEF_SECTIONS)


def test_field_to_db_dict_consistency() -> None:
    for field, db in FIELD_TO_DB.items():
        assert DB_TO_FIELD[db] == field


def test_section_headings_returns_six_pairs_in_order() -> None:
    headings = section_headings()
    assert len(headings) == 6
    assert headings[0] == ("Founders", "founders")
    assert headings[4] == ("Risk Flags", "risk_flags")


def test_build_brief_from_grouped_fills_missing_with_empty_lists() -> None:
    grouped = {
        "founders": [BriefClaim(claim_text="t", quoted_span="q", source_chunk_id="c")],
    }
    brief = build_brief_from_grouped(grouped)
    assert isinstance(brief, Brief)
    assert len(brief.founders) == 1
    assert brief.company == []
    assert brief.risk_flags == []


def test_brief_pydantic_field_set_matches_brief_fields() -> None:
    """Adding a Pydantic field without updating BRIEF_SECTIONS would silently break."""
    pydantic_fields = set(Brief.model_fields.keys())
    schema_fields = set(BRIEF_FIELDS)
    assert pydantic_fields == schema_fields
