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
  - Phase 3: adds `LangChain CallbackHandler` for LangGraph nodes (Plan 03-07)
  - Phase 3: adds `redact_for_logging()` PII helper (D-08, Plan 03-09 integrates)
  - Phase 3: Lambda handlers add `flush_and_shutdown(lambda_sleep=True)`

References:
  - STACK.md §2.6 (Langfuse OpenTelemetry-first; Lambda flush pattern)
  - CLAUDE.md locked decisions (Langfuse 3.x+, not OpenTelemetry direct)
  - .claude/skills/langfuse/references/instrumentation.md (baseline + common mistakes)
  - 03-CONTEXT.md D-03 (CallbackHandler trace_context attachment), D-08 (PII regex scope)
"""
from __future__ import annotations

import logging
import pathlib
import re
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# Resolve repo root from this file: backend/src/dossier/observability.py
# parents: [0]=dossier, [1]=src, [2]=backend, [3]=repo root
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

_EnvTriple = tuple[str, str, str]  # (public_key, secret_key, host)


def load_env(env_path: pathlib.Path | None = None) -> None:
    """Backward-compat shim. Settings (pydantic-settings) auto-loads the
    repo-root .env on every Settings() instantiation, so calling this is
    no longer necessary. Kept as a no-op for any external scripts that
    import it (langfuse_smoke.py used to call this explicitly).

    `env_path` is honored by patching dotenv directly, for the rare test
    that points at a fixture .env file rather than the canonical one.
    """
    if env_path is not None and env_path.exists():
        load_dotenv(env_path)


def read_langfuse_env(strict: bool = True) -> _EnvTriple:
    """Return (public_key, secret_key, host). In strict mode, raise on missing keys.

    Non-strict mode returns empty strings for missing credentials — callers
    then decide whether to no-op or error. The smoke test uses strict=True.
    FastAPI handlers (Phase 2+) use strict=False so a missing Langfuse key
    doesn't crash request handling — tracing degrades gracefully.

    Implementation: thin wrapper over `Settings`. Returns SecretStr-extracted
    plain strings so existing callers (which pass these into the langfuse
    SDK constructor as positional/keyword strs) keep working.
    """
    from dossier.core.settings import get_settings  # noqa: PLC0415 — defer to avoid import cycle
    settings = get_settings()
    public_key = (
        settings.langfuse_public_key.get_secret_value()
        if settings.langfuse_public_key is not None
        else ""
    ).strip()
    secret_key = (
        settings.langfuse_secret_key.get_secret_value()
        if settings.langfuse_secret_key is not None
        else ""
    ).strip()
    host = settings.langfuse_host.strip()

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


# ---------------------------------------------------------------------------
# LangChain CallbackHandler for per-node LangGraph spans (D-03, PLAT-04)
# ---------------------------------------------------------------------------


def get_langchain_callback_handler(
    trace_id: str | None = None,
    session_id: str | None = None,  # noqa: ARG001 — reserved for caller metadata wiring
    *,
    strict: bool = False,
) -> Any | None:
    """Return a Langfuse LangChain CallbackHandler for LangGraph node tracing.

    D-03 (03-CONTEXT.md): One span per graph node. Spans attach to the
    existing ``investigations.langfuse_trace_id`` by passing ``trace_id``
    to the handler constructor via ``trace_context``.

    Langfuse 4.x API (installed version 4.5.0):
        from langfuse.langchain import CallbackHandler
        CallbackHandler(trace_context={"trace_id": trace_id})

    The 4.x ``CallbackHandler`` constructor only accepts ``public_key`` and
    ``trace_context`` kwargs — session_id is NOT a constructor parameter in
    4.x. Callers that want a session label should set it on the graph
    invocation ``config={"metadata": {"langfuse_session_id": ...}}`` (the
    Langfuse LangChain integration reads this metadata key from the run
    context). We accept ``session_id`` in our signature for forward-compat
    callsites (runner.py), but we ignore it at the handler level — see
    runner.py for where it is actually plumbed via config["metadata"].

    Returns None if Langfuse credentials are absent and ``strict=False``,
    or if handler construction raises for any reason. In Lambda, Langfuse
    outages must NOT stall an investigation (T-03-07-02 mitigation).

    Usage:
        handler = get_langchain_callback_handler(
            trace_id=investigation.langfuse_trace_id,
            session_id=str(investigation_id),
        )
        config = {
            "callbacks": [handler] if handler else [],
            "metadata": {"langfuse_session_id": str(investigation_id)},
            "configurable": {"thread_id": str(investigation_id)},
        }
        await graph.ainvoke(state, config=config)
    """
    try:
        # Deferred import — env must be loaded first (see module docstring #1).
        # Langfuse 4.x moved the LangChain integration to langfuse.langchain
        # (3.x used langfuse.callback.CallbackHandler, which no longer exists).
        public_key, secret_key, _host = read_langfuse_env(strict=strict)
        if not public_key or not secret_key:
            # Non-strict: no creds → no tracing. Graph continues without spans.
            return None

        from langfuse.langchain import CallbackHandler  # noqa: PLC0415

        handler_kwargs: dict[str, Any] = {}
        if trace_id:
            handler_kwargs["trace_context"] = {"trace_id": trace_id}

        return CallbackHandler(**handler_kwargs)
    except Exception:  # noqa: BLE001
        logger.warning(
            "observability: failed to create Langfuse CallbackHandler — "
            "node spans will be missing for this run (graph continues)",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# PII redaction for pre-Langfuse / pre-log emission (GUARD-04, D-08)
# ---------------------------------------------------------------------------
#
# Regex-only patterns per 03-CONTEXT.md D-08. Applied ONLY on emission paths
# (Langfuse span inputs/outputs, log records); NEVER at DB write — citation
# grounding does substring matching against raw `source_chunks.text`, so
# redacting there would break INVEST-06 quote-span lookup.
#
# Covered:
#   - Email addresses (RFC-5322 lite)
#   - E.164 international phone numbers (+ then 7-15 digits)
#   - Common US phone (+1 with optional separators, or raw 10-digit area-code)
#   - SSN-shaped 9-digit (XXX-XX-XXXX or XXX XX XXXX or XXX.XX.XXXX)
#   - Clerk user IDs (user_<alphanumeric>) — Clerk-specific identifier
#   - $amounts with 9+ leading digits (adversarial injection surface; real
#     public-source corpora do not contain $100M+ raw integer strings)
#
# LLM-judge PII detection explicitly deferred (D-09, Phase 4+).

_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Email — conservative RFC-ish; won't match quoted-local-part edge cases
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    # US phone with +1 country code + separators: +1 (415) 555-2671, +1-415-555-2671, etc.
    (
        re.compile(r"\+1[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "[PHONE]",
    ),
    # Raw US 10-digit with separators: (415) 555-2671, 415-555-2671, 415.555.2671
    (
        re.compile(r"\(\d{3}\)[-.\s]?\d{3}[-.\s]?\d{4}\b"),
        "[PHONE]",
    ),
    (
        re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"),
        "[PHONE]",
    ),
    # SSN-shaped nine digits with separators: 123-45-6789 / 123 45 6789 / 123.45.6789
    (re.compile(r"\b\d{3}[-.\s]\d{2}[-.\s]\d{4}\b"), "[SSN]"),
    # Clerk user IDs — Clerk emits user_<base62> identifiers; never wanted in traces
    (re.compile(r"\buser_[A-Za-z0-9]{10,}\b"), "[CLERK_USER_ID]"),
    # Dollar amounts with 9+ digits (no comma) — injection-surface sentinel
    (re.compile(r"\$\d{9,}\b"), "[AMOUNT]"),
    # Catch-all E.164 international phone (+ then 7-15 digits, no separators)
    # Runs LAST so it does not eat already-redacted US phones above.
    (re.compile(r"\+\d{7,15}\b"), "[PHONE]"),
]


def redact_for_logging(text: str) -> str:
    """Redact PII from text before emitting to Langfuse traces or logs.

    D-08 (03-CONTEXT.md): regex-only, pre-Langfuse emission only.
    Does NOT redact at DB write — citation precision requires raw text
    in ``source_chunks.text`` for INVEST-06 substring matching.

    Patterns applied in order:
        email → phone (US + E.164) → SSN-shaped → Clerk user IDs → $amount
    Non-string or None input returns empty string to keep callers from
    needing to type-guard every span-emit callsite.

    LLM-judge PII detection is explicitly deferred to Phase 4+ (D-09) —
    regex is strictly better for well-defined PII shapes; adds no value
    for email/phone/SSN and would cost a call per emission.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        # Defensive: callers may pass dict/list via Langfuse span metadata;
        # stringify first so downstream str.sub does not raise.
        text = str(text)

    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


__all__ = [
    "flush_and_shutdown",
    "get_langchain_callback_handler",
    "get_langfuse_client",
    "load_env",
    "read_langfuse_env",
    "redact_for_logging",
]
