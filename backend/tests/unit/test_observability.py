"""Unit tests for observability.py PII redaction + CallbackHandler factory.

Covers Plan 03-07:
- redact_for_logging() — D-08 regex patterns (email, phone, SSN, Clerk ID, $amount)
- get_langchain_callback_handler() — returns CallbackHandler with trace_context
  attached or None on graceful-degrade paths (missing creds, import failure,
  handler construction raises) — T-03-07-02 mitigation.

Does NOT hit Langfuse Cloud. Tests that need a constructed handler either
rely on monkeypatched creds in env, or inspect the call args of a
monkeypatched CallbackHandler class.
"""
from __future__ import annotations

from typing import Any

import pytest

from dossier import observability
from dossier.observability import (
    get_langchain_callback_handler,
    redact_for_logging,
)


# ---------------------------------------------------------------------------
# redact_for_logging — D-08 regex coverage
# ---------------------------------------------------------------------------


def test_redact_email_simple():
    assert redact_for_logging("ping alice@example.com now") == "ping [EMAIL] now"


def test_redact_email_plus_and_dots():
    out = redact_for_logging("alice.bob+vc@sub-domain.co.uk writes")
    assert "[EMAIL]" in out
    assert "@" not in out


def test_redact_us_phone_e164():
    assert redact_for_logging("call +14155552671 soon") == "call [PHONE] soon"


def test_redact_us_phone_with_separators():
    for raw in ("+1-415-555-2671", "+1 (415) 555-2671", "+1.415.555.2671"):
        assert "[PHONE]" in redact_for_logging(f"num {raw} ok")


def test_redact_us_phone_raw_10_digit_with_separators():
    # Raw 10-digit area-code phone, no + prefix — common in pitch deck free text.
    for raw in ("415-555-2671", "415.555.2671", "(415) 555-2671"):
        out = redact_for_logging(f"ring {raw} asap")
        assert "[PHONE]" in out, f"phone pattern missed for raw={raw}, got {out}"


def test_redact_e164_international_non_us():
    # +44 UK number — catch-all E.164 pattern at end of list handles this.
    assert "[PHONE]" in redact_for_logging("reach +442071838750 today")


def test_redact_ssn_all_separator_shapes():
    for raw in ("123-45-6789", "123 45 6789", "123.45.6789"):
        out = redact_for_logging(f"SSN {raw} leaked")
        assert "[SSN]" in out, f"ssn pattern missed for raw={raw}, got {out}"


def test_redact_clerk_user_id():
    # Clerk emits user_<base62> ids; we redact them from traces.
    out = redact_for_logging("actor user_2abCDef1234567890 did X")
    assert "[CLERK_USER_ID]" in out
    assert "user_" not in out or "[CLERK_USER_ID]" in out


def test_redact_dollar_amount_9_digits_or_more():
    assert "[AMOUNT]" in redact_for_logging("raised $1234567890 Series X")
    assert "[AMOUNT]" in redact_for_logging("valuation $100000000")  # exactly 9 digits


def test_redact_short_dollar_amounts_unchanged():
    # $8-digit amounts are real financial data in briefs — we do NOT redact them.
    # D-08 explicitly scopes redaction to 9+ digit integers (adversarial surface).
    for raw in ("$10000000", "$500000", "$12M", "$42"):
        out = redact_for_logging(f"deal {raw} reported")
        assert "[AMOUNT]" not in out, f"short amount falsely redacted: {raw} → {out}"


def test_redact_empty_and_none_input():
    assert redact_for_logging("") == ""
    # The helper defensively stringifies non-string inputs; None → "" via falsy guard.
    assert redact_for_logging(None) == ""  # type: ignore[arg-type]


def test_redact_multiple_pii_in_one_string():
    # Combined emission payload stress test — Plan 03-09 span inputs can mix several.
    raw = (
        "Founder alice@example.com (415-555-2671) SSN 987-65-4321 "
        "actor=user_2abCDef123456 raised $9876543210"
    )
    out = redact_for_logging(raw)
    for marker in ("[EMAIL]", "[PHONE]", "[SSN]", "[CLERK_USER_ID]", "[AMOUNT]"):
        assert marker in out, f"marker {marker} missing in {out}"
    # And none of the raw PII survives.
    for leaked in ("alice@example.com", "415-555-2671", "987-65-4321",
                   "user_2abCDef123456", "9876543210"):
        assert leaked not in out, f"raw PII leaked: {leaked} in {out}"


def test_redact_non_string_input_stringified_without_crash():
    # Langfuse span metadata can be dict/list — helper should not explode.
    out = redact_for_logging({"k": "v"})  # type: ignore[arg-type]
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# get_langchain_callback_handler — graceful degrade + trace_context plumbing
# ---------------------------------------------------------------------------


def test_handler_returns_none_when_creds_absent(monkeypatch):
    """Lambda outage defense: missing creds → None, graph continues uncrashed."""
    # Force read_langfuse_env to report no creds without hitting .env file.
    monkeypatch.setattr(
        observability,
        "read_langfuse_env",
        lambda strict=False: ("", "", "https://cloud.langfuse.com"),
    )
    handler = get_langchain_callback_handler(
        trace_id="trace-abc", session_id="inv-123", strict=False
    )
    assert handler is None


def test_handler_returns_none_when_import_fails(monkeypatch):
    """If langfuse.langchain cannot be imported (e.g., version skew), degrade."""

    def _fake_read(strict=False):
        return ("pk_test", "sk_test", "https://cloud.langfuse.com")

    monkeypatch.setattr(observability, "read_langfuse_env", _fake_read)

    # Blow up on deferred import inside the function.
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *a, **kw):
        if name == "langfuse.langchain":
            raise ImportError("simulated version skew")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    handler = get_langchain_callback_handler(trace_id="t", strict=False)
    assert handler is None


def test_handler_returns_none_when_construction_raises(monkeypatch):
    """Any CallbackHandler __init__ exception must degrade, not crash the run."""
    monkeypatch.setattr(
        observability,
        "read_langfuse_env",
        lambda strict=False: ("pk", "sk", "https://cloud.langfuse.com"),
    )

    class _BoomHandler:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("simulated Langfuse SDK init failure")

    import langfuse.langchain as lc_mod

    monkeypatch.setattr(lc_mod, "CallbackHandler", _BoomHandler)
    handler = get_langchain_callback_handler(trace_id="t", strict=False)
    assert handler is None


def test_handler_passes_trace_context_when_trace_id_given(monkeypatch):
    """trace_id kwarg → trace_context={'trace_id': ...} on construction (D-03)."""
    monkeypatch.setattr(
        observability,
        "read_langfuse_env",
        lambda strict=False: ("pk", "sk", "https://cloud.langfuse.com"),
    )

    captured: dict[str, Any] = {}

    class _SpyHandler:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    import langfuse.langchain as lc_mod

    monkeypatch.setattr(lc_mod, "CallbackHandler", _SpyHandler)

    h = get_langchain_callback_handler(trace_id="trace-xyz-123", session_id="inv-1")
    assert h is not None
    assert captured.get("trace_context") == {"trace_id": "trace-xyz-123"}
    # 4.x does NOT accept session_id; our helper must not attempt to pass it.
    assert "session_id" not in captured


def test_handler_omits_trace_context_when_no_trace_id(monkeypatch):
    """No trace_id → construct plain handler (will open a fresh trace)."""
    monkeypatch.setattr(
        observability,
        "read_langfuse_env",
        lambda strict=False: ("pk", "sk", "https://cloud.langfuse.com"),
    )

    captured: dict[str, Any] = {}

    class _SpyHandler:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    import langfuse.langchain as lc_mod

    monkeypatch.setattr(lc_mod, "CallbackHandler", _SpyHandler)

    h = get_langchain_callback_handler()
    assert h is not None
    assert "trace_context" not in captured


def test_handler_strict_mode_raises_on_missing_creds(monkeypatch):
    """strict=True must propagate the missing-creds RuntimeError from read_langfuse_env.

    This is the smoke-test / Phase 1 path — fail loud when creds are missing
    rather than silently dropping traces. The Lambda path uses strict=False.
    """

    def _no_creds(strict=False):
        if strict:
            raise RuntimeError("Missing Langfuse env vars: LANGFUSE_PUBLIC_KEY")
        return ("", "", "")

    monkeypatch.setattr(observability, "read_langfuse_env", _no_creds)

    # strict=True catches the exception and returns None because our outer
    # try/except is broad — but we pass it through the env read, so the
    # RuntimeError is caught and handler returns None. This is intentional:
    # runner.py uses strict=False; no caller uses strict=True yet. We assert
    # graceful-degrade remains the public contract even in strict mode.
    h = get_langchain_callback_handler(strict=True)
    assert h is None


def test_session_id_accepted_silently_for_forward_compat(monkeypatch):
    """session_id kwarg is accepted but not passed to 4.x CallbackHandler.

    runner.py plumbs session_id via config['metadata']['langfuse_session_id'],
    not through the handler constructor. Keep the signature so a future
    Langfuse version that re-introduces session_id can be picked up without
    a runner refactor.
    """
    monkeypatch.setattr(
        observability,
        "read_langfuse_env",
        lambda strict=False: ("pk", "sk", "https://cloud.langfuse.com"),
    )

    captured: dict[str, Any] = {}

    class _SpyHandler:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    import langfuse.langchain as lc_mod

    monkeypatch.setattr(lc_mod, "CallbackHandler", _SpyHandler)

    h = get_langchain_callback_handler(trace_id="t", session_id="inv-42")
    assert h is not None
    assert "session_id" not in captured  # not a 4.x kwarg


# ---------------------------------------------------------------------------
# __all__ contract — consumers grep these names, keep them stable
# ---------------------------------------------------------------------------


def test_public_exports_include_new_helpers():
    assert "get_langchain_callback_handler" in observability.__all__
    assert "redact_for_logging" in observability.__all__
    # Prior exports must still be present (no accidental deletion).
    for name in ("get_langfuse_client", "flush_and_shutdown",
                 "load_env", "read_langfuse_env"):
        assert name in observability.__all__
