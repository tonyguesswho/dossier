"""Compatibility wrapper for `dossier.scripts.langfuse_smoke`."""
from dossier.scripts.langfuse_smoke import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
