"""Tests for brief_to_markdown — the brief renderer."""
from __future__ import annotations

from dossier.investigate.brief_schema import Brief, BriefClaim, build_brief_from_grouped
from dossier.investigate.render import brief_to_markdown


def _claim(text: str, chunk_id: str = "c1") -> BriefClaim:
    return BriefClaim(claim_text=text, quoted_span=text, source_chunk_id=chunk_id)


def test_renders_six_section_headings_in_canonical_order() -> None:
    md = brief_to_markdown(build_brief_from_grouped({}), {})
    headings = [line for line in md.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Founders",
        "## Company",
        "## Market",
        "## Product",
        "## Risk Flags",
        "## Suggested Questions",
    ]


def test_empty_section_renders_placeholder() -> None:
    md = brief_to_markdown(build_brief_from_grouped({}), {})
    assert "_No claims synthesized for this section._" in md


def test_known_chunk_id_renders_source_link() -> None:
    brief = build_brief_from_grouped({"founders": [_claim("Founded 2024", "c1")]})
    md = brief_to_markdown(brief, {"c1": "https://example.com/about"})
    assert "- Founded 2024 ([source](https://example.com/about))" in md


def test_unknown_chunk_id_omits_link_but_keeps_claim() -> None:
    brief = build_brief_from_grouped({"founders": [_claim("Founded 2024", "missing")]})
    md = brief_to_markdown(brief, {})
    assert "- Founded 2024" in md
    assert "[source]" not in md
