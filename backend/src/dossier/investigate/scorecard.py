"""Citation precision + grounding rate over an investigation's claims.

Pure function over the rows the repository returns. Lives outside the
routes module because the same computation is reachable from CLI eval
reports and (eventually) a Langfuse score callback.

grounded_span_start/end on the row are SOURCE-absolute; chunk_text is
chunk-local — translate via chunk_char_start before slicing.
"""
from __future__ import annotations

from typing import Iterable

from dossier.core.text_normalize import normalize


def compute_scorecard(rows: Iterable) -> dict | None:
    """Return scorecard dict, or None when there are no claims to score.

    Output keys match `dossier.api.schemas.ScorecardResponse` field names so
    the route can splat directly: `ScorecardResponse(**result)`.
    """
    rows = list(rows)
    if not rows:
        return None

    total = len(rows)
    grounded = 0
    hits = 0

    for r in rows:
        if (
            r.chunk_id
            and r.chunk_text is not None
            and r.grounded_span_start is not None
        ):
            grounded += 1
            local_start = r.grounded_span_start - (r.chunk_char_start or 0)
            local_end = r.grounded_span_end - (r.chunk_char_start or 0)
            quoted = r.chunk_text[local_start:local_end]
            if quoted:
                nq = normalize(quoted)
                nc = normalize(r.chunk_text)
                if nq and nq in nc:
                    hits += 1

    precision = (hits / grounded) if grounded else 0.0
    grounding_rate = (grounded / total) if total else 0.0
    return {
        "citation_precision": precision,
        "grounding_rate": grounding_rate,
        "total_claims": total,
        "grounded_claims": grounded,
    }


__all__ = ["compute_scorecard"]
