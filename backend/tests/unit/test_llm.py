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
    strong_model,
)
from dossier.core.settings import Settings


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
# Case 3: Settings raises ValidationError on missing OPENROUTER_API_KEY.
# Replaces the legacy `read_openrouter_env(strict=True)` raise check —
# the validation now happens at process start, not per-call.
# ---------------------------------------------------------------------------
def test_settings_requires_openrouter_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Block .env file fallback so monkeypatch.delenv actually takes effect.
    monkeypatch.setattr(
        "dossier.core.settings.Settings.model_config",
        {**Settings.model_config, "env_file": None},
    )
    with pytest.raises(Exception, match="openrouter_api_key|OPENROUTER_API_KEY"):
        Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Case 4: strong_model() builds an OpenAI client with correct base_url
# ---------------------------------------------------------------------------
def test_strong_model_configures_openrouter_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-fake")
    client = strong_model()
    # openai>=2.7 exposes base_url on the client (as a URL-like object with str()).
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
