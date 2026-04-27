"""Integration test for GET /healthz via FastAPI TestClient.

No DB, no external API, no Clerk config required — exercises the app factory
plus CORS middleware. This is the smoke test that proves `uv run uvicorn
dossier.api.main:app` would come up cleanly.
Target runtime: <500ms including TestClient startup.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dossier.api.main import app


@pytest.mark.integration
def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


@pytest.mark.integration
def test_healthz_does_not_require_auth() -> None:
    """Health checks must work without a bearer token (Phase 3 Lambda probe)."""
    client = TestClient(app)
    response = client.get("/healthz")  # no Authorization header
    assert response.status_code == 200


@pytest.mark.integration
def test_cors_headers_allow_localhost_3000() -> None:
    """CORS middleware must permit the Next.js dev server."""
    client = TestClient(app)
    response = client.options(
        "/healthz",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code in (200, 204)
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
