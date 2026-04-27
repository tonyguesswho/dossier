from __future__ import annotations

from dossier.investigate.brief_schema import Brief, BriefClaim, section_headings


def brief_to_markdown(brief: Brief, url_by_chunk: dict[str, str]) -> str:
    # url_by_chunk should cover the WHOLE corpus, not just top-k —
    # the grounder may pin to a chunk outside the synthesizer's window.
    out: list[str] = []
    for heading, field in section_headings():
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

