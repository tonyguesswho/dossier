"""Brief markdown rendering."""
from __future__ import annotations

from dossier.models import Brief, BriefClaim


_SECTION_HEADINGS: list[tuple[str, str]] = [
    ("Founders", "founders"),
    ("Company", "company"),
    ("Market", "market"),
    ("Product", "product"),
    ("Risk Flags", "risk_flags"),
    ("Suggested Questions", "suggested_questions"),
]


def brief_to_markdown(brief: Brief, url_by_chunk: dict[str, str]) -> str:
    """Render a Brief as markdown with `([source](url))` citations.

    `url_by_chunk` should cover every chunk in the investigation's corpus,
    not only the top-k passed to the synthesizer — the grounder may pin a
    claim to a chunk outside the top-k. Unknown chunk_ids render as
    `(source)` without a link rather than dropping the citation.
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
        out.append("")
    return "\n".join(out)


__all__ = ["brief_to_markdown"]
