"""Core shared infra for Dossier backend: llm, db, exceptions.

Every api/* and investigate/* module imports from here. This package is the
single source of truth for OpenRouter access (CLAUDE.md + PLAT-03), SQLAlchemy
engine creation (PATTERNS.md §"DATABASE_URL single source of truth"), and
cross-cutting exception types.

Phase coverage:
  - Phase 2: llm.py, db.py, exceptions.py land here.
  - Phase 3: add cheap_model() body (currently a placeholder) + split into
    two Lambda-container targets that each import from dossier.core per D-15.
"""
