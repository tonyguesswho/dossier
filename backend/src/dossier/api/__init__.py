"""FastAPI surface for Dossier.

This package owns the api-lambda scope per CONTEXT.md D-15. Phase 2 runs
everything in one uvicorn process; Phase 3 splits into two Lambda container
images — one targeting `dossier.api.main:app` (thin), one targeting
`dossier.investigate.pipeline:handler` (heavy). Zero Python refactor between
phases; the split is a Dockerfile + Terraform concern only.

Phase coverage:
  - Phase 2: uvicorn local dev; single FastAPI app serving all routes.
  - Phase 3: same module becomes api-lambda entry point; investigate-lambda
    is a sibling container targeting dossier.investigate.pipeline:handler.
"""
