from __future__ import annotations
# Single Langfuse entry point. Don't `from langfuse import ...` elsewhere —
# the deferred imports here are what keep env loading correct.

import logging
import pathlib
import re
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_EnvTriple = tuple[str, str, str]  # (public_key, secret_key, host)


def load_env(env_path: pathlib.Path | None = None) -> None:
    # No-op for prod (Settings reads .env); kept so test fixtures pointing at custom .env still work.
    if env_path is not None and env_path.exists():
        load_dotenv(env_path)


def read_langfuse_env(strict: bool = True) -> _EnvTriple:
    # strict=False returns empty strings so handlers can degrade to a no-op tracer.
    from dossier.core.settings import get_settings  # noqa: PLC0415
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
            "Missing Langfuse env vars: " + ", ".join(missing)
        )
    return public_key, secret_key, host


def get_langfuse_client(strict: bool = True) -> Any:
    # Read env BEFORE importing langfuse — its eager env read at import time misses runtime load_dotenv.
    read_langfuse_env(strict=strict)
    from langfuse import get_client  # noqa: PLC0415

    return get_client()


def flush_and_shutdown(client: Any, *, lambda_sleep: bool = False) -> None:
    # lambda_sleep=True: 15s drain before container freeze. Without it ~30% of traces are lost.
    try:
        client.flush()
    except Exception:  # noqa: BLE001
        logger.warning("langfuse: flush failed", exc_info=True)
    try:
        client.shutdown()
    except Exception:  # noqa: BLE001
        logger.warning("langfuse: shutdown failed", exc_info=True)

    if lambda_sleep:
        import time  # noqa: PLC0415

        time.sleep(15)


def get_langchain_callback_handler(
    trace_id: str | None = None,
    session_id: str | None = None,  # noqa: ARG001 — accepted for symmetry; langfuse 4.x reads it from run metadata
    *,
    strict: bool = False,
) -> Any | None:
    # Returns None on missing creds / construction failure — Langfuse outage must not stall a run.
    try:
        public_key, secret_key, _host = read_langfuse_env(strict=strict)
        if not public_key or not secret_key:
            return None

        from langfuse.langchain import CallbackHandler  # noqa: PLC0415

        handler_kwargs: dict[str, Any] = {}
        if trace_id:
            handler_kwargs["trace_context"] = {"trace_id": trace_id}

        return CallbackHandler(**handler_kwargs)
    except Exception:  # noqa: BLE001
        logger.warning(
            "observability: CallbackHandler init failed; node spans skipped",
            exc_info=True,
        )
        return None


# Regex-only PII scrub for emission paths (Langfuse spans, log records).
# NEVER apply at DB write — citation grounding does substring matching
# against raw source_chunks.text, so redacting there breaks quote lookup.
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\+1[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\(\d{3}\)[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b\d{3}[-.\s]\d{2}[-.\s]\d{4}\b"), "[SSN]"),
    (re.compile(r"\buser_[A-Za-z0-9]{10,}\b"), "[CLERK_USER_ID]"),
    (re.compile(r"\$\d{9,}\b"), "[AMOUNT]"),
    # E.164 catch-all runs last so it doesn't eat already-redacted US phones.
    (re.compile(r"\+\d{7,15}\b"), "[PHONE]"),
]


def redact_for_logging(text: str) -> str:
    if not text:
        return ""
    if not isinstance(text, str):
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
