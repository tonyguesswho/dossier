"""Auth dependency for FastAPI routes.

Verifies the Clerk JWT forwarded by the Next.js proxy and returns the
`sub` claim as the canonical user_id. Falls back to a dev-bypass env
var for local testing without a Clerk project.
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


# The guard is built lazily so importing the app doesn't require Clerk
# credentials (lets uvicorn boot under dev-bypass mode).
_CLERK_GUARD_CACHE: ClerkHTTPBearer | None = None
_CLERK_GUARD_BUILT: bool = False


def _reset_clerk_guard_cache() -> None:
    """Test hook — re-read CLERK_JWKS_URL on next request."""
    global _CLERK_GUARD_CACHE, _CLERK_GUARD_BUILT
    _CLERK_GUARD_CACHE = None
    _CLERK_GUARD_BUILT = False


def _get_clerk_guard() -> ClerkHTTPBearer | None:
    """Return a cached ClerkHTTPBearer, or None if no JWKS URL configured."""
    global _CLERK_GUARD_CACHE, _CLERK_GUARD_BUILT
    if _CLERK_GUARD_BUILT:
        return _CLERK_GUARD_CACHE

    jwks_url = get_settings().clerk_jwks_url.strip()
    if jwks_url:
        # auto_error=False so we can return our own 401 shape rather than
        # fastapi-clerk-auth's default 403.
        _CLERK_GUARD_CACHE = ClerkHTTPBearer(
            ClerkConfig(jwks_url=jwks_url), auto_error=False
        )
    _CLERK_GUARD_BUILT = True
    return _CLERK_GUARD_CACHE


async def _verify_clerk_credentials(
    request: Request | None,
    creds: Optional[HTTPAuthorizationCredentials],
) -> str:
    """Verify a Clerk JWT and return its `sub` claim. Raises 401 on failure."""
    dev_bypass = get_settings().dossier_auth_dev_bypass.strip()
    if dev_bypass:
        return dev_bypass

    guard = _get_clerk_guard()
    if guard is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_clerk_credentials",
        )

    if creds is None and request is not None:
        creds = await guard(request)

    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing_clerk_credentials",
        )

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
    """FastAPI dependency: returns the Clerk user_id or raises 401.

    Falls back to DOSSIER_AUTH_DEV_BYPASS for local testing — never set
    that env var in production.
    """
    return await _verify_clerk_credentials(request, None)


__all__ = ["_verify_clerk_credentials", "require_clerk_user_id"]
