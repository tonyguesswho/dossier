"""Langfuse observability setup for Dossier.

This module is the single entry point for Langfuse wiring across the whole
backend — scripts, CLI tools, FastAPI handlers, LangGraph nodes. Nothing else
should `from langfuse import ...` directly.

Why a dedicated module (per the langfuse skill's instrumentation guidance):
  1. **Env-before-import safety**: Langfuse reads credentials from env the
     moment `get_client()` is called. If env isn't loaded first, the client
     initializes with missing credentials and silently drops traces. This
     module enforces `load_dotenv()` before any langfuse import.
  2. **Import-order safety for OpenAI**: Phase 2+ will use
     `from langfuse.openai import openai` to auto-trace OpenRouter calls —
     that import MUST happen before any other `openai` import. Centralizing
     here ensures we can't accidentally import the stock openai client first.
  3. **Flush discipline**: Every exit path must call `flush() + shutdown()`
     or traces are lost. Lambda adds `time.sleep(15)` on top (Phase 3).
     Wrapping in one helper prevents that gotcha from leaking into handlers.
  4. **Graceful-degrade policy**: If creds are absent, return a no-op client
     rather than raising. Phase 1 scripts need the smoke test to error loudly,
     but Phase 2+ FastAPI handlers must NOT crash on a Langfuse outage.

Phase coverage:
  - Phase 1: smoke test via `scripts/langfuse_smoke.py` (connectivity check)
  - Phase 2: will add `langfuse.openai` drop-in wrapper
  - Phase 3: will add `LangChain CallbackHandler` for LangGraph nodes
  - Phase 3: Lambda handlers add `flush_and_shutdown(lambda_sleep=True)`

References:
  - STACK.md §2.6 (Langfuse 3.x OpenTelemetry-first; Lambda flush pattern)
  - CLAUDE.md locked decisions (Langfuse 3.x, not OpenTelemetry direct)
  - .claude/skills/langfuse/references/instrumentation.md (baseline + common mistakes)
"""
from __future__ import annotations

import logging
import os
import pathlib
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# Resolve repo root from this file: backend/src/dossier/observability.py
# parents: [0]=dossier, [1]=src, [2]=backend, [3]=repo root
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

_EnvTriple = tuple[str, str, str]  # (public_key, secret_key, host)


def load_env(env_path: pathlib.Path | None = None) -> None:
    """Load .env before any langfuse import. Idempotent and safe to call repeatedly.

    Uses repo-root .env by default. Tests and Lambda handlers can override the path.
    """
    target = env_path if env_path is not None else (_REPO_ROOT / ".env")
    if target.exists():
        load_dotenv(target)
    # If .env is absent, rely on env vars already in the shell (e.g., Lambda).


def read_langfuse_env(strict: bool = True) -> _EnvTriple:
    """Return (public_key, secret_key, host). In strict mode, raise on missing keys.

    Non-strict mode returns empty strings for missing credentials — callers
    then decide whether to no-op or error. The smoke test uses strict=True.
    FastAPI handlers (Phase 2+) use strict=False so a missing Langfuse key
    doesn't crash request handling — tracing degrades gracefully.
    """
    load_env()
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com").strip()

    if strict and (not public_key or not secret_key):
        missing = [
            name
            for name, value in (
                ("LANGFUSE_PUBLIC_KEY", public_key),
                ("LANGFUSE_SECRET_KEY", secret_key),
            )
            if not value
        ]
        raise RuntimeError(
            "Missing Langfuse env vars: "
            + ", ".join(missing)
            + ". Copy .env.example to .env and paste keys from "
            "cloud.langfuse.com → Project Settings → API Keys."
        )
    return public_key, secret_key, host


def get_langfuse_client(strict: bool = True) -> Any:
    """Return the Langfuse client singleton.

    Enforces env-before-import: load_dotenv happens inside read_langfuse_env
    BEFORE the `from langfuse import get_client` import. If we imported langfuse
    at module top-level, its eager env read would miss values landed by
    load_dotenv at call time.
    """
    read_langfuse_env(strict=strict)
    from langfuse import get_client  # noqa: PLC0415  (deferred import is intentional)

    return get_client()


def flush_and_shutdown(client: Any, *, lambda_sleep: bool = False) -> None:
    """Flush buffered traces and shut down the client cleanly.

    Args:
        client: The Langfuse client from `get_langfuse_client()`.
        lambda_sleep: If True, sleep 15s after shutdown per STACK.md §2.6.
            Only set True in Lambda handlers (Phase 3+). The sleep prevents
            the Lambda exec context freezing before in-flight HTTP completes.
            For local scripts and long-running servers this is NOT needed.
    """
    try:
        client.flush()
    except Exception:  # noqa: BLE001
        logger.warning("langfuse: flush failed — some traces may be lost", exc_info=True)
    try:
        client.shutdown()
    except Exception:  # noqa: BLE001
        logger.warning("langfuse: shutdown failed", exc_info=True)

    if lambda_sleep:
        import time  # noqa: PLC0415

        time.sleep(15)


__all__ = [
    "flush_and_shutdown",
    "get_langfuse_client",
    "load_env",
    "read_langfuse_env",
]
