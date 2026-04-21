"""Filesystem-location per CONTEXT.md D-18.

Actual implementation is at dossier.scripts.langfuse_smoke so it's importable
under the package, not just runnable from a path.

Usage:
    cd backend
    uv run python -m dossier.scripts.langfuse_smoke
"""
from dossier.scripts.langfuse_smoke import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
