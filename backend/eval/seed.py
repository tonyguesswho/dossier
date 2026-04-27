"""Compatibility wrapper for `dossier.eval.seed`."""
from dossier.eval.seed import main, upsert_eval_items

__all__ = ["main", "upsert_eval_items"]

if __name__ == "__main__":
    raise SystemExit(main())
