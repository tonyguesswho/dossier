"""Filesystem-location re-export per CONTEXT.md D-18.

D-18 places the seed script at `backend/eval/seed.py`. The actual implementation lives
at `backend/src/dossier/eval/seed.py` so it's importable as `dossier.eval.seed` (enables
`python -m dossier.eval.seed`).

Future-Anthony reading this file finds a pointer to the real implementation rather than
a missing file.
"""
from dossier.eval.seed import main, upsert_eval_items

__all__ = ["main", "upsert_eval_items"]

if __name__ == "__main__":
    raise SystemExit(main())
