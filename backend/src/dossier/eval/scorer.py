"""Deterministic citation-precision scorer.

Pure function: no DB, no LLM, no network. Every quoted_span is normalized
and tested as a substring of the cited chunk's text.

Normalization: lowercase → collapse whitespace runs → strip leading/trailing
punctuation. Same rule grounding uses (ground.py imports `normalize` from here)
so eval-time and grounding-time scores can't drift.
"""
from __future__ import annotations

import logging
import re
import string

from dossier.models import Claim

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")

# string.punctuation: !"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~
# Plus the smart-quote / em-dash / ellipsis characters seen in web copy.
_SMART_PUNCT = "‘’“”–—…"
_STRIP_CHARS = string.punctuation + string.whitespace + _SMART_PUNCT


def _normalize(text: str) -> str:
    lowered = text.lower()
    collapsed = _WHITESPACE_RE.sub(" ", lowered)
    return collapsed.strip(_STRIP_CHARS)


# Function-object identity reference — ground.py imports `normalize` and any
# change to `_normalize` propagates automatically. Don't reassign.
normalize = _normalize


def citation_precision(claims: list[Claim], corpus: dict[str, str]) -> float:
    """Fraction of claims whose quoted_span appears (normalized) in its
    cited chunk. Empty claims list returns 1.0 with a warning — most likely
    an upstream bug where the synthesizer produced no rows.
    """
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
        norm_quote = _normalize(claim.quoted_span)
        norm_source = _normalize(chunk_text)
        if norm_quote and norm_quote in norm_source:
            hits += 1

    return hits / len(claims)


__all__ = ["citation_precision", "normalize"]
