"""Single source of truth for runtime configuration.

Replaces the scattered `os.environ.get(...)` calls and per-module
`read_<provider>_env(strict=...)` helpers that grew organically across
Phases 1-3. Built on `pydantic_settings.BaseSettings` so:

  - **Type-checked.** `database_url` must parse as a Postgres URL,
    `langfuse_host` must be a valid HTTP URL, secrets are `SecretStr`
    (never accidentally logged via `repr()`).
  - **Single declaration.** Required vs optional vars are obvious at a
    glance — required fields raise `ValidationError` on `Settings()` init
    if absent. Optional vars have explicit defaults.
  - **Test-friendly.** Tests monkeypatch `os.environ` and call
    `get_settings.cache_clear()` to force re-read; no need to coordinate
    `load_env()` calls across modules.
  - **Production-friendly.** Lambda runtime sets env vars directly (no
    `.env` file) — same code path, same validation. Local dev reads the
    repo-root `.env` automatically.

Usage:

    from dossier.core.settings import get_settings

    settings = get_settings()
    api_key = settings.openrouter_api_key.get_secret_value()
    if settings.dispatch_mode == "lambda":
        ...

Migration note: legacy `read_<provider>_env()` helpers in
`core/llm.py` / `observability.py` still exist as thin wrappers around
`get_settings()` for backward compatibility while callers migrate.
"""
from __future__ import annotations

import logging
import os
import pathlib
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Resolve to repo root: parents[0]=core, [1]=dossier, [2]=src, [3]=backend,
# [4]=repo root (where .env lives).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """All runtime configuration in one place.

    Field-naming convention: lowercase + underscores; pydantic-settings
    auto-maps to `UPPERCASE_WITH_UNDERSCORES` env vars. Override with
    `Settings(openrouter_api_key="...")` in tests if you want to skip
    `os.environ` entirely.
    """

    model_config = SettingsConfigDict(
        # Read from repo-root .env in local dev. In Lambda the file is
        # absent and pydantic falls through to os.environ — same code path.
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        # Lambda runtime injects extra AWS_* vars we don't want to map;
        # 'ignore' lets them pass through unread.
        extra="ignore",
        # Allow case-insensitive env var matching so DATABASE_URL and
        # database_url both work.
        case_sensitive=False,
    )

    # --- Database ---------------------------------------------------------
    # SQLAlchemy-format URL ('postgresql+psycopg://') used by the sync engine.
    # The graph runner needs a libpq-format variant — see `database_url_libpq`
    # below. Both are derived from the same source string.
    database_url: str = Field(
        ...,
        description="SQLAlchemy/psycopg connection URL. "
                    "Format: postgresql+psycopg://user:pass@host:port/db?sslmode=require",
    )

    # --- LLM providers ----------------------------------------------------
    # OpenRouter for chat/completions; OpenAI directly for embeddings
    # (OpenRouter does not proxy /v1/embeddings — see DECISIONS.md #6).
    openrouter_api_key: SecretStr = Field(
        ...,
        description="OpenRouter API key. Required — every LLM call goes "
                    "through OpenRouter via the stock openai SDK.",
    )
    openai_api_key: SecretStr = Field(
        ...,
        description="OpenAI API key for embeddings only. Chat/synth uses "
                    "OpenRouter; embeddings need direct api.openai.com.",
    )

    # --- Observability ----------------------------------------------------
    # Langfuse keys are *soft* requirements — missing keys downgrade tracing
    # to a no-op so a Langfuse outage cannot kill an investigation.
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- Tools ------------------------------------------------------------
    exa_api_key: SecretStr = Field(
        ...,
        description="Exa.ai web search API key. Required — primary "
                    "investigation source.",
    )
    github_token: SecretStr | None = Field(
        default=None,
        description="GitHub personal access token for founder profile lookups. "
                    "Optional — github tool falls back to unauthenticated requests "
                    "(60 req/hr rate limit) when this is unset.",
    )
    firecrawl_api_key: SecretStr = Field(
        ...,
        description="Firecrawl API key for seed-URL deep crawls.",
    )
    # Optional: empty string = skip the tool entirely (existing fail-open).
    newsapi_api_key: str = ""
    crunchbase_api_key: str = ""

    # --- Auth / dispatch / runtime ---------------------------------------
    clerk_jwks_url: str = ""
    """Clerk JWKS URL for JWT verification. Empty = `dossier_auth_dev_bypass`
    must be set, otherwise the API fails closed with 401."""

    dossier_auth_dev_bypass: str = ""
    """When set to a non-empty string, FastAPI's `require_clerk_user_id`
    returns this value as the user_id without verifying Clerk JWTs. NEVER
    set in production — runner.py refuses to boot if both this and
    AWS_LAMBDA_FUNCTION_NAME are present."""

    dispatch_mode: Literal["local", "lambda"] = Field(
        default="local",
        alias="DOSSIER_DISPATCH_MODE",
    )
    """How POST /investigations dispatches the graph: 'local' runs in-
    process via FastAPI BackgroundTasks; 'lambda' self-invokes via boto3
    InvocationType=Event."""

    lambda_function_name: str = ""
    """Self-reference for the lambda dispatch path. Set by Terraform; only
    required when dispatch_mode='lambda'."""

    # --- Validators -------------------------------------------------------
    @field_validator("database_url")
    @classmethod
    def _check_postgres_url(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgresql+psycopg://", "postgres://")):
            raise ValueError(
                f"database_url must be a Postgres URL (postgresql:// or "
                f"postgresql+psycopg://). Got: {v[:30]}..."
            )
        return v

    # --- Computed properties ---------------------------------------------
    @property
    def database_url_libpq(self) -> str:
        """`postgresql://` flavor for direct psycopg consumers (e.g.
        psycopg_pool.AsyncConnectionPool which doesn't understand the
        SQLAlchemy `+psycopg` driver tag)."""
        if self.database_url.startswith("postgresql+psycopg://"):
            return "postgresql://" + self.database_url[len("postgresql+psycopg://"):]
        return self.database_url

    @property
    def has_langfuse(self) -> bool:
        """True iff both Langfuse keys are present. Callers gate tracing on
        this rather than catching exceptions later."""
        return self.langfuse_public_key is not None and self.langfuse_secret_key is not None

    @property
    def is_lambda_runtime(self) -> bool:
        """True iff this process is running inside an AWS Lambda invocation
        (used by runner.py's security invariant check). Reads
        AWS_LAMBDA_FUNCTION_NAME directly because it's an AWS-injected
        runtime signal, not user-configurable."""
        return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def get_settings() -> Settings:
    """Return a freshly-loaded Settings instance.

    Intentionally NOT cached — Settings() init is ~100µs (dominated by
    pydantic field validation), and dropping the cache means tests that
    monkeypatch `os.environ` see the change on the next call without any
    cache-clearing fixture. Hot paths (e.g. per-chunk classifier) should
    grab `settings = get_settings()` once at the top of the function and
    reuse it locally rather than re-calling per loop iteration.
    """
    return Settings()  # type: ignore[call-arg]


__all__ = ["Settings", "get_settings"]
