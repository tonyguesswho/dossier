"""Filesystem-location re-export per CONTEXT.md D-05.

D-05 requires `backend/eval/scorer.py` to exist as the canonical location.
The implementation actually lives in the importable `dossier.eval.scorer` package
(because `backend/eval/` is not a Python package — it's a sibling of `backend/src/`).

Phase 4's eval pipeline imports via the package path:
    from dossier.eval.scorer import citation_precision

This file exists so future-Anthony reading `backend/eval/scorer.py` (the D-05 location)
finds a pointer to the real implementation rather than a missing file.
"""
from dossier.eval.scorer import citation_precision

__all__ = ["citation_precision"]
