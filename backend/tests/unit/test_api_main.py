"""Unit tests for the dossier.api.main module factory and Clerk dependency.

These cases lock the app-factory contract set by
.planning/phases/02-single-pass-rag-pipeline/02-03-PLAN.md Task 1:

  1. `app` is importable from dossier.api.main and is a FastAPI instance.
  2. App metadata (title/version) matches plan spec.
  3. CORS middleware is registered.
  4. require_clerk_user_id is importable from dossier.api.dependencies.
  5. Dev bypass mode returns the env var value verbatim.
  6. No JWKS + no bypass + no creds → 401 missing_clerk_credentials.

D-15: api module is the single api-lambda entry point; Phase 3 splits deployment.
D-24: session auth model — JWT verified in FastAPI, sub claim is Clerk user_id.

Tests run against import-level artifacts only — no HTTP, no network.
Target runtime: <200ms for the full file.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Case 1: FastAPI app object is constructed at module level
# ---------------------------------------------------------------------------
def test_app_is_fastapi_instance() -> None:
    from fastapi import FastAPI

    from dossier.api.main import app

    assert isinstance(app, FastAPI)


# ---------------------------------------------------------------------------
# Case 2: App title + version match CONTEXT / PLAN spec
# ---------------------------------------------------------------------------
def test_app_metadata_matches_plan() -> None:
    from dossier.api.main import app

    assert app.title == "Dossier API"
    assert app.version == "0.1.0"


# ---------------------------------------------------------------------------
# Case 3: CORS middleware is registered (Next.js dev at localhost:3000)
# ---------------------------------------------------------------------------
def test_cors_middleware_registered() -> None:
    from fastapi.middleware.cors import CORSMiddleware

    from dossier.api.main import app

    # user_middleware is the FastAPI/Starlette list of Middleware(cls, options=...) entries.
    middleware_classes = [mw.cls for mw in app.user_middleware]
    assert CORSMiddleware in middleware_classes, (
        f"CORSMiddleware missing from app.user_middleware — got {middleware_classes}"
    )


# ---------------------------------------------------------------------------
# Case 4: require_clerk_user_id is importable and a callable dependency
# ---------------------------------------------------------------------------
def test_require_clerk_user_id_importable() -> None:
    from dossier.api.dependencies import require_clerk_user_id

    assert callable(require_clerk_user_id)


# ---------------------------------------------------------------------------
# Case 5: Dev bypass env var short-circuits auth and returns its value
# ---------------------------------------------------------------------------
def test_require_clerk_user_id_dev_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOSSIER_AUTH_DEV_BYPASS", "user_test_bypass_id")
    # Ensure no JWKS is set — bypass path must not require the guard to be configured.
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)

    from dossier.api.dependencies import require_clerk_user_id

    result = require_clerk_user_id(creds=None)
    assert result == "user_test_bypass_id"


# ---------------------------------------------------------------------------
# Case 6: fail-closed — no JWKS, no bypass, no creds → 401
# ---------------------------------------------------------------------------
def test_require_clerk_user_id_fail_closed_without_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOSSIER_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)

    from fastapi import HTTPException

    from dossier.api.dependencies import require_clerk_user_id

    with pytest.raises(HTTPException) as exc_info:
        require_clerk_user_id(creds=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "missing_clerk_credentials"


# ---------------------------------------------------------------------------
# Case 7: missing bearer with JWKS configured → 401 missing_clerk_credentials
# ---------------------------------------------------------------------------
def test_require_clerk_user_id_missing_bearer_with_jwks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Even with JWKS set, if creds is None (no Authorization header) we fail closed.
    monkeypatch.delenv("DOSSIER_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setenv(
        "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
    )

    from fastapi import HTTPException

    from dossier.api.dependencies import require_clerk_user_id

    with pytest.raises(HTTPException) as exc_info:
        require_clerk_user_id(creds=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "missing_clerk_credentials"


# ---------------------------------------------------------------------------
# Case 8: malformed JWT payload (no sub claim) → 401 invalid_clerk_jwt
# ---------------------------------------------------------------------------
def test_require_clerk_user_id_invalid_jwt_no_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOSSIER_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setenv(
        "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
    )

    from fastapi import HTTPException
    from fastapi_clerk_auth import HTTPAuthorizationCredentials

    from dossier.api.dependencies import require_clerk_user_id

    # Simulate a decoded JWT missing the `sub` claim.
    fake_creds = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="fake.jwt.token",
        decoded={"aud": "clerk", "iat": 123},  # no `sub`
    )
    with pytest.raises(HTTPException) as exc_info:
        require_clerk_user_id(creds=fake_creds)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid_clerk_jwt"


# ---------------------------------------------------------------------------
# Case 9: valid creds with sub claim → returns the Clerk user_id string
# ---------------------------------------------------------------------------
def test_require_clerk_user_id_valid_creds_returns_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOSSIER_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setenv(
        "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
    )

    from fastapi_clerk_auth import HTTPAuthorizationCredentials

    from dossier.api.dependencies import require_clerk_user_id

    fake_creds = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="fake.jwt.token",
        decoded={"sub": "user_2abc123", "aud": "clerk"},
    )
    result = require_clerk_user_id(creds=fake_creds)
    assert result == "user_2abc123"
