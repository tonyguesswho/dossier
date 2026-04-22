"""GET /healthz — liveness probe.

Intentionally public (no Clerk auth). Phase 3 Lambda + ALB/API Gateway health
checks hit this path; if it requires a JWT the probe fails. Threat model:
the endpoint returns a static payload with no user data, so anonymous access
is fine (CONTEXT.md threat model is deferred to Plan 02-09 for protected routes).

Why a dedicated module (vs. inlining in main.py):
  - Every route lives under dossier.api.routes.* so new routes ship as new
    files rather than growing main.py (CONTEXT.md D-15 module boundaries).
  - Phase 3's api-lambda Dockerfile wants main.py to stay small — routing
    pointers only — so cold-start import time is minimal.

Rejected alternatives:
  - `/health` (no z): Kubernetes convention is `/healthz`; even though Phase 2
    runs uvicorn locally, matching the convention future-proofs Phase 3 Lambda
    + potential ALB health checks.
  - Include DB ping: liveness must succeed even when Postgres is down (it's
    a separate concern from readiness). Plan 02-09 may add a `/readyz`
    variant that does check DB; keep `/healthz` pure-liveness.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe. Returns 200 with version + status even if DB is down."""
    return {"status": "ok", "version": "0.1.0"}
