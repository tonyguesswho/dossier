from __future__ import annotations

import logging

from dossier.core.text_normalize import normalize
from dossier.models import Claim

logger = logging.getLogger(__name__)


def citation_precision(claims: list[Claim], corpus: dict[str, str]) -> float:
    if not claims:
        logger.warning(
            "citation_precision: 0 claims — returning 1.0 (vacuously perfect)"
        )
        return 1.0

    hits = 0
    for claim in claims:
        chunk_text = corpus.get(claim.source_chunk_id)
        if chunk_text is None:
            continue
        norm_quote = normalize(claim.quoted_span)
        norm_source = normalize(chunk_text)
        if norm_quote and norm_quote in norm_source:
            hits += 1

    return hits / len(claims)

