"""Clerk auth dependency tests (no HTTP, no network)."""
from __future__ import annotations

import pytest


async def test_require_clerk_user_id_dev_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev bypass env var short-circuits auth and returns its value."""
    monkeypatch.setenv("DOSSIER_AUTH_DEV_BYPASS", "user_test_bypass_id")
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)

    from dossier.api.dependencies import _reset_clerk_guard_cache, _verify_clerk_credentials

    _reset_clerk_guard_cache()
    result = await _verify_clerk_credentials(request=None, creds=None)
    assert result == "user_test_bypass_id"


async def test_require_clerk_user_id_fail_closed_without_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No JWKS, no bypass, no creds → 401."""
    monkeypatch.delenv("DOSSIER_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.delenv("CLERK_JWKS_URL", raising=False)

    from fastapi import HTTPException

    from dossier.api.dependencies import _reset_clerk_guard_cache, _verify_clerk_credentials

    _reset_clerk_guard_cache()
    with pytest.raises(HTTPException) as exc_info:
        await _verify_clerk_credentials(request=None, creds=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "missing_clerk_credentials"


async def test_require_clerk_user_id_missing_bearer_with_jwks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JWKS configured but no Authorization header → 401 missing_clerk_credentials."""
    monkeypatch.delenv("DOSSIER_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setenv(
        "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
    )

    from fastapi import HTTPException

    from dossier.api.dependencies import _reset_clerk_guard_cache, _verify_clerk_credentials

    _reset_clerk_guard_cache()
    with pytest.raises(HTTPException) as exc_info:
        await _verify_clerk_credentials(request=None, creds=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "missing_clerk_credentials"


async def test_require_clerk_user_id_invalid_jwt_no_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decoded JWT missing the `sub` claim → 401 invalid_clerk_jwt."""
    monkeypatch.delenv("DOSSIER_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setenv(
        "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
    )

    from fastapi import HTTPException
    from fastapi_clerk_auth import HTTPAuthorizationCredentials

    from dossier.api.dependencies import _reset_clerk_guard_cache, _verify_clerk_credentials

    _reset_clerk_guard_cache()
    fake_creds = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="fake.jwt.token",
        decoded={"aud": "clerk", "iat": 123},
    )
    with pytest.raises(HTTPException) as exc_info:
        await _verify_clerk_credentials(request=None, creds=fake_creds)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "invalid_clerk_jwt"


async def test_require_clerk_user_id_valid_creds_returns_sub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid creds with sub claim → returns the Clerk user_id."""
    monkeypatch.delenv("DOSSIER_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setenv(
        "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
    )

    from fastapi_clerk_auth import HTTPAuthorizationCredentials

    from dossier.api.dependencies import _reset_clerk_guard_cache, _verify_clerk_credentials

    _reset_clerk_guard_cache()
    fake_creds = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="fake.jwt.token",
        decoded={"sub": "user_2abc123", "aud": "clerk"},
    )
    result = await _verify_clerk_credentials(request=None, creds=fake_creds)
    assert result == "user_2abc123"


async def test_clerk_guard_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Module-level cache must skip PyJWKClient rebuild on every request."""
    monkeypatch.setenv(
        "CLERK_JWKS_URL", "https://example.clerk.accounts.dev/.well-known/jwks.json"
    )

    from dossier.api.dependencies import _get_clerk_guard, _reset_clerk_guard_cache

    _reset_clerk_guard_cache()
    guard1 = _get_clerk_guard()
    guard2 = _get_clerk_guard()
    assert guard1 is guard2
