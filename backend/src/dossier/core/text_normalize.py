from __future__ import annotations

import re
import string

_WHITESPACE_RE = re.compile(r"\s+")

# Smart-quote / em-dash / ellipsis seen in web copy, plus standard punctuation.
_SMART_PUNCT = "‘’“”–—…"
_STRIP_CHARS = string.punctuation + string.whitespace + _SMART_PUNCT


def normalize(text: str) -> str:
    # Single source of truth for citation matching: ground.py and eval.scorer must not drift.
    lowered = text.lower()
    collapsed = _WHITESPACE_RE.sub(" ", lowered)
    return collapsed.strip(_STRIP_CHARS)


__all__ = ["normalize"]
