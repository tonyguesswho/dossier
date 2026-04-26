"""Canonical citation-matching text normalization.

Single source of truth for the rule that decides whether a quoted span
matches a source chunk. Both grounding (`investigate.ground`) and eval
scoring (`eval.scorer`) call into here so the two cannot drift.

Rule: lowercase → collapse whitespace runs to a single space → strip
leading/trailing punctuation (ASCII + smart quotes/dashes/ellipsis).
"""
from __future__ import annotations

import re
import string

_WHITESPACE_RE = re.compile(r"\s+")

# string.punctuation: !"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~
# Plus the smart-quote / em-dash / ellipsis characters seen in web copy.
_SMART_PUNCT = "‘’“”–—…"
_STRIP_CHARS = string.punctuation + string.whitespace + _SMART_PUNCT


def normalize(text: str) -> str:
    lowered = text.lower()
    collapsed = _WHITESPACE_RE.sub(" ", lowered)
    return collapsed.strip(_STRIP_CHARS)


__all__ = ["normalize"]
