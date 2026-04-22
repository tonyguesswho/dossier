"""Investigation pipeline for Dossier — tools, ingest, retrieve, synthesize, ground, pipeline.

Per CONTEXT.md D-15 this package corresponds to the `investigate-lambda` scope.
Phase 2 runs inside the same uvicorn process as dossier.api; Phase 3 splits this
module into its own Lambda container image with entry point
`dossier.investigate.pipeline:handler`.
"""
