"""Deterministic citation-precision scorer for Dossier.

Contract (CONTEXT.md D-06):
    def citation_precision(claims: list[Claim], corpus: dict[str, str]) -> float

This is a pure function: no DB, no LLM, no network. It is Pitfall 1.1's programmatic
defense — every quoted_span is checked against the cited chunk's raw text.

Normalization rule (CONTEXT.md D-07):
  1. lowercase both sides
  2. collapse whitespace runs (\\s+) to a single space
  3. strip leading/trailing punctuation
  4. then test: normalized(quoted_span) in normalized(source_text_for_chunk)

Edge cases:
  - Empty claims list → return 1.0 AND log a warning (D-09). Does not raise.
  - Claim references a source_chunk_id not in corpus → scores 0 for that claim.

Rejected alternatives:
  - LLM-as-judge: hides the decision (STACK.md §2.7); brittle; not reproducible.
  - Token-level fuzzy match (rapidfuzz / Levenshtein): would mask paraphrase-as-citation
    (Pitfall 1.3) — the metric is whether the exact quote is in the source.
  - Regex / word-boundary matching: normalized substring is simpler and matches what
    a panelist does with ctrl-F (Pitfall 1.1 example scenario).
"""
from __future__ import annotations

import logging
import re
import string

from dossier.models import Claim

logger = logging.getLogger(__name__)

# Pre-compiled regex for collapsing whitespace runs (D-07 step 2).
_WHITESPACE_RE = re.compile(r"\s+")

# Leading/trailing punctuation characters to strip (D-07 step 3).
# `string.punctuation` includes: !"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~
# Plus common "smart quote" / curly unicode punctuation seen in web copy.
_SMART_PUNCT = "‘’“”–—…"  # ' ' " " – — …
_STRIP_CHARS = string.punctuation + string.whitespace + _SMART_PUNCT


def _normalize(text: str) -> str:
    """Apply the D-07 normalization pipeline."""
    # Step 1: lowercase
    lowered = text.lower()
    # Step 2: collapse whitespace runs to single space
    collapsed = _WHITESPACE_RE.sub(" ", lowered)
    # Step 3: strip leading/trailing punctuation (and whitespace, for safety)
    stripped = collapsed.strip(_STRIP_CHARS)
    return stripped


# Public re-export. D-07 contract lock: `dossier.investigate.ground` imports this
# so grounding uses the SAME rule Phase 4 eval uses. Drift here silently breaks
# eval — the same chunk text, normalized two different ways, would compute
# different precision scores depending on whether you asked ingest-time or
# eval-time. The alias is a function-object identity ref (`normalize is _normalize`),
# so any change to `_normalize` propagates to ground.py automatically.
normalize = _normalize


def citation_precision(claims: list[Claim], corpus: dict[str, str]) -> float:
    """Fraction of claims whose quoted_span appears (normalized) in the cited chunk.

    Args:
        claims: List of Claim objects. Each must have `quoted_span` and `source_chunk_id`.
        corpus: Mapping of source_chunk_id → raw chunk text.

    Returns:
        hits / len(claims) — a float in [0.0, 1.0].
        If claims is empty, returns 1.0 and logs a warning (D-09).
    """
    if not claims:
        logger.warning(
            "citation_precision called with 0 claims; returning 1.0 (vacuously perfect). "
            "This likely indicates an upstream bug — synthesized briefs should have claims."
        )
        return 1.0

    hits = 0
    for claim in claims:
        chunk_text = corpus.get(claim.source_chunk_id)
        if chunk_text is None:
            # Unknown source_chunk_id — scores 0 (D-10 case 5).
            continue
        norm_quote = _normalize(claim.quoted_span)
        norm_source = _normalize(chunk_text)
        if norm_quote and norm_quote in norm_source:
            hits += 1

    return hits / len(claims)


__all__ = ["citation_precision", "normalize"]
