"""FastAPI dependencies for Dossier.

Central place for auth plumbing; Plan 02-09 will add `get_db_session` here
for row-scoped SQLAlchemy sessions. Route modules import from this module
rather than reconstructing the dependency chain inline.

D-24 session auth model:
  - Next.js middleware validates Clerk session on the browser side.
  - Next.js route handler forwards a short-lived Clerk JWT to FastAPI as Bearer.
  - FastAPI verifies via fastapi-clerk-auth and extracts the `sub` claim
    (Clerk user_id).
  - All row-scoped queries apply WHERE user_id = :clerk_user_id (D-24; enforced
    in Plan 02-09).

Rejected alternatives:
  - Verify Clerk session on every request via Clerk's backend SDK HTTP call:
    adds a network round-trip per request; JWKS local verification is faster
    and matches STACK.md §2.9.
  - Skip Lambda-side auth (trust the Next.js proxy): violates ARCHITECTURE.md
    §10 "Verify session in Lambda too — don't trust the proxy blindly."
  - Module-level singleton guard: makes testing painful because env changes
    after import don't rebuild the guard. We build lazily on first use and
    cache, then allow explicit reset via _reset_clerk_guard_cache() for tests.

Phase coverage:
  - Phase 2: require_clerk_user_id (this module). Dev-bypass flag for local
    testing before Clerk keys are wired.
  - Phase 2 Plan 02-09: adds get_db_session (SQLAlchemy sessionmaker dep).
  - Phase 3: no shape change — the same dependency runs in api-lambda.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request, status
from fastapi_clerk_auth import (
    ClerkConfig,
    ClerkHTTPBearer,
    HTTPAuthorizationCredentials,
)

from dossier.core.settings import get_settings

# Module-level cache for the ClerkHTTPBearer guard. Built lazily on first
# request that reaches require_clerk_user_id so that:
#   (a) `from dossier.api.main import app` never requires CLERK_JWKS_URL
#       (lets uvicorn boot with dev bypass configured instead).
#   (b) Tests can monkeypatch CLERK_JWKS_URL and call _reset_clerk_guard_cache
#       to force a rebuild.
_CLERK_GUARD_CACHE: ClerkHTTPBearer | None = None
_CLERK_GUARD_BUILT: bool = False


def _reset_clerk_guard_cache() -> None:
    """Clear the cached Clerk guard. Only for tests that flip CLERK_JWKS_URL."""
    global _CLERK_GUARD_CACHE, _CLERK_GUARD_BUILT
    _CLERK_GUARD_CACHE = None
    _CLERK_GUARD_BUILT = False


def _get_clerk_guard() -> ClerkHTTPBearer | None:
    """Return the cached ClerkHTTPBearer guard or build+cache it lazily.

    Returns None if CLERK_JWKS_URL is not set — callers must fail closed
    in that branch unless DOSSIER_AUTH_DEV_BYPASS is active.
    """
    global _CLERK_GUARD_CACHE, _CLERK_GUARD_BUILT
    if _CLERK_GUARD_BUILT:
        return _CLERK_GUARD_CACHE

    jwks_url = get_settings().clerk_jwks_url.strip()
    if not jwks_url:
        _CLERK_GUARD_CACHE = None
    else:
        # auto_error=False so a missing Authorization header yields creds=None
        # rather than FastAPI raising a 403 before we can normalize to our
        # canonical 401 "missing_clerk_credentials" shape.
        _CLERK_GUARD_CACHE = ClerkHTTPBearer(
            ClerkConfig(jwks_url=jwks_url), auto_error=False
        )
    _CLERK_GUARD_BUILT = True
    return _CLERK_GUARD_CACHE


async def _verify_clerk_credentials(
    request: Request | None, creds: Optional[HTTPAuthorizationCredentials]
) -> str:
    """Core verification logic shared by the FastAPI dep and direct-call tests.

    Not exposed as a FastAPI dependency. The public entry point
    `require_clerk_user_id` wraps this with a FastAPI-friendly signature
    (single `request` param) that doesn't confuse FastAPI's body-field
    introspection when used as a sub-dependency via `Depends(...)`.
    """
    dev_bypass = get_settings().dossier_auth_dev_bypass.strip()
    if dev_bypass:
        return dev_bypass

    guard = _get_clerk_guard()
    if guard is None:
        # No JWKS configured AND no dev bypass — fail closed.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_clerk_credentials",
        )

    # If called via FastAPI DI and no explicit creds were injected, run the
    # guard against the incoming request to extract and verify the JWT.
    if creds is None and request is not None:
        creds = await guard(request)

    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_clerk_credentials",
        )

    # creds.decoded is the JWT payload dict per fastapi_clerk_auth source.
    # When the guard returned creds with decoded=None, the JWT failed verification.
    payload = creds.decoded
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_clerk_jwt",
        )
    user_id = payload.get("sub")
    if not user_id or not isinstance(user_id, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_clerk_jwt",
        )
    return user_id


async def require_clerk_user_id(request: Request) -> str:
    """Return the verified Clerk user_id (sub claim) or raise 401.

    FastAPI dependency: `user_id: str = Depends(require_clerk_user_id)`.
    Signature is intentionally minimal (only `request: Request`) so FastAPI's
    sub-dependency introspection does not try to materialize other parameters
    as body fields on every route that depends on this function.

    Tests that need to exercise the verification branches deterministically
    should import and call `_verify_clerk_credentials(request, creds)` directly —
    that's the kernel this wrapper delegates to.

    DEV-ONLY bypass: if DOSSIER_AUTH_DEV_BYPASS is set to a non-empty string,
    return its value verbatim. NEVER set DOSSIER_AUTH_DEV_BYPASS in deployed
    envs; it short-circuits auth entirely. Documented in .env.example.

    Production behavior (no bypass, CLERK_JWKS_URL set):
      - Missing bearer: 401 {"detail": "missing_clerk_credentials"}
      - Invalid signature or malformed (no sub claim): 401 {"detail": "invalid_clerk_jwt"}
      - Valid: returns the `sub` claim string (e.g., "user_2abc123").

    No JWKS configured AND no bypass → fail closed with 401
    missing_clerk_credentials. This prevents a misconfigured deploy from
    accidentally serving unauthenticated requests.
    """
    return await _verify_clerk_credentials(request, None)


__all__ = ["_verify_clerk_credentials", "require_clerk_user_id"]
