"""Unit tests for dossier.core.llm — the OpenRouter entry point.

Locked by .planning/phases/02-single-pass-rag-pipeline/02-CONTEXT.md:
  PLAT-03 / CLAUDE.md: OpenRouter via stock openai SDK with base_url override.
  D-09: three named model slots; cheap_model reserved for Phase 3.

Tests run against env monkeypatch only — no real OpenRouter call.
Target runtime: <100ms for the full file.
"""
from __future__ import annotations

import pytest

from dossier.core.llm import (
    CHEAP_MODEL_ID,
    EMBEDDING_MODEL_ID,
    OPENROUTER_BASE_URL,
    STRONG_MODEL_ID,
    cheap_model,
    embedding_model,
    read_openrouter_env,
    strong_model,
)


# ---------------------------------------------------------------------------
# Case 1: locked constants — PLAT-03 / STACK.md §2.5 / schema VECTOR(1536)
# ---------------------------------------------------------------------------
def test_locked_constants() -> None:
    assert OPENROUTER_BASE_URL == "https://openrouter.ai/api/v1"
    assert STRONG_MODEL_ID == "anthropic/claude-sonnet-4.5"
    assert EMBEDDING_MODEL_ID == "text-embedding-3-small"
    assert CHEAP_MODEL_ID == "anthropic/claude-haiku-4.5"


# ---------------------------------------------------------------------------
# Case 2: embedding_model / cheap_model return str literals
# ---------------------------------------------------------------------------
def test_embedding_and_cheap_model_getters() -> None:
    assert embedding_model() == "text-embedding-3-small"
    assert cheap_model() == "anthropic/claude-haiku-4.5"


# ---------------------------------------------------------------------------
# Case 3: strict=True raises on missing key (D-09 fail-fast for synthesizer)
# ---------------------------------------------------------------------------
def test_strict_raises_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Also block load_env from reading .env in CI — point it at an empty temp dir.
    monkeypatch.setattr("dossier.core.llm.load_env", lambda: None)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY not set"):
        read_openrouter_env(strict=True)


# ---------------------------------------------------------------------------
# Case 4: strict=False returns empty string on missing key (graceful degrade)
# ---------------------------------------------------------------------------
def test_non_strict_returns_empty_on_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("dossier.core.llm.load_env", lambda: None)
    assert read_openrouter_env(strict=False) == ""


# ---------------------------------------------------------------------------
# Case 5: strong_model() builds an OpenAI client with correct base_url
# ---------------------------------------------------------------------------
def test_strong_model_configures_openrouter_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-fake")
    monkeypatch.setattr("dossier.core.llm.load_env", lambda: None)
    client = strong_model()
    # openai>=2.7 exposes base_url on the client (as a URL-like object with str()).
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
