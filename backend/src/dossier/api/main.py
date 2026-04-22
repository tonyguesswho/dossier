"""FastAPI app factory for Dossier backend.

This module is the single entry point for the api-lambda scope (CONTEXT.md D-15).
Frontend route handlers at `frontend/app/api/investigations/*` forward Clerk-
authenticated requests here. Business logic stays server-side per
ARCHITECTURE.md §9 anti-pattern "Put business logic in Next.js route handlers"
(CONTEXT.md D-23).

Why a dedicated module:
  1. **One app, one place.** Wave 2 Plan 02-09 mounts the investigations router
     onto this same `app` object. Phase 3 splits deployment, not source code.
  2. **Clerk + CORS wired once.** Every route inherits auth and CORS middleware.
     Avoids per-route CORS drift that would break the Next.js dev proxy silently.
  3. **Dev uvicorn entry point.** `uv run uvicorn dossier.api.main:app` is the
     canonical local-dev command (CONTEXT.md D-01). Phase 3 swaps in Mangum
     without touching main.py.

Rejected alternatives:
  - Put CORS per-route: loses middleware coherence; a missed route leaks
    no-CORS responses that break the Next.js dev proxy.
  - Construct the app in a factory function: FastAPI's uvicorn entry expects
    a module-level app variable; factory complicates the CLI invocation.
  - Keep `/healthz` inline in main.py: every new route would grow this file.
    Route modules live under dossier.api.routes.* so new routes ship as new
    files (CONTEXT.md D-15 module boundaries).

Phase coverage:
  - Phase 2: uvicorn local dev; single FastAPI app for api + investigate concerns.
  - Phase 3: this same module becomes the entry point for api-lambda; a second
    Dockerfile targets dossier.investigate.pipeline:handler per CONTEXT.md D-15.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dossier.api.routes import health, investigations

app = FastAPI(title="Dossier API", version="0.1.0")

# CORS — Phase 2 pins the Next.js dev server origin. Phase 3 replaces this
# with an env-driven origin list when the Vercel deployment URL is known.
# Wildcard origin is never used — CONTEXT.md threat model T-02-03-04.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(investigations.router)


__all__ = ["app"]
