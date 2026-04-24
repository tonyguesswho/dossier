"""Brief rendering helpers shared by the graph's finalize node and any
legacy callers. Extracted from `pipeline.py` so the render contract is not
coupled to the (retired) linear pipeline.

Public surface:
  - HINT_SEPARATOR: the token used to pack an optional context hint into
    the investigations.input_ref column. Value encoded in Phase 2 Plan
    02-09's route handler; consumers (chat.py, runner.py, investigations.py)
    split on this token to recover (value, hint).
  - brief_to_markdown(brief, url_by_chunk): deterministic section-by-section
    render of a Brief, with ([source](url)) suffixes resolved via the
    url_by_chunk map. Empty sections render with an explicit "no claims
    synthesized" placeholder (D-05 fail-fast — never fabricate content to
    fill an empty section).
"""
from __future__ import annotations

from dossier.models import Brief, BriefClaim


HINT_SEPARATOR: str = "\n---HINT---\n"


# Field name (plural risk_flags, suggested_questions) → display heading.
_SECTION_HEADINGS: list[tuple[str, str]] = [
    ("Founders", "founders"),
    ("Company", "company"),
    ("Market", "market"),
    ("Product", "product"),
    ("Risk Flags", "risk_flags"),
    ("Suggested Questions", "suggested_questions"),
]


def brief_to_markdown(brief: Brief, url_by_chunk: dict[str, str]) -> str:
    """Deterministic markdown render: 6 sections, bullet-per-claim, inline
    citations resolved via url_by_chunk.

    Phase 4 (BRIEF-02) replaces this with inline citation popovers. Phase 2-3
    stay on plain bullets with a `([source](url))` suffix when the cited
    chunk's url is known. Unknown `source_chunk_id` values render as
    `(source)` without a link.

    url_by_chunk is a chunk_id → url map covering ALL source_chunks for the
    investigation (not just the top-k retrieved set). Built by the caller
    via a SQL JOIN so grounded claims whose chunk_id lives outside the
    retrieved set still get a clickable link — this was the pre-Phase-4
    Facebook-brief bug where every claim rendered as literal `(source)`.
    """
    out: list[str] = []
    for heading, field in _SECTION_HEADINGS:
        out.append(f"## {heading}\n")
        claims: list[BriefClaim] = getattr(brief, field)
        if not claims:
            out.append("_No claims synthesized for this section._\n")
            continue
        for c in claims:
            url = url_by_chunk.get(c.source_chunk_id, "")
            suffix = f" ([source]({url}))" if url else ""
            out.append(f"- {c.claim_text}{suffix}")
        out.append("")  # blank line between sections
    return "\n".join(out)


__all__ = ["HINT_SEPARATOR", "brief_to_markdown"]
