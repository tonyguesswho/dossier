"""OpenRouter entry point for Dossier.

This module is the single entry point for LLM and embedding access across the
whole backend — api/* handlers, investigate/* pipeline nodes, scripts, tests.
Nothing else should `from openai import ...` directly.

Why a dedicated module (ARCHITECTURE.md §9 anti-pattern defense):
  1. **One place for model IDs.** Model changes require exactly one edit, not a
     grep-sweep. Three named roles (`strong_model`, `embedding_model`, `cheap_model`)
     decouple callers from specific model strings.
  2. **Env-before-client-construct safety.** read_openrouter_env() calls load_env()
     before the OpenAI client instantiates — mirrors observability.py's pattern.
  3. **PLAT-03 / CLAUDE.md contract.** OpenRouter is accessed via the stock `openai`
     SDK with base_url override. Never a dedicated OpenRouter client (python-openrouter
     is a thin wrapper that lags openai SDK, STACK.md §2.2).
  4. **Graceful-degrade policy.** strict=False returns empty key; callers decide.

Rejected alternatives:
  - python-openrouter library: thin wrapper, lags openai SDK, broken streaming (STACK.md §2.2).
  - Separate provider SDKs (anthropic-sdk, google-genai): fragments model routing.
  - Module-level singleton client: interferes with load_env call-timing.

Phase coverage:
  - Phase 2: strong_model() for synthesize, embedding_model() for ingest chunks.
  - Phase 3: cheap_model() wired to the LangGraph planner + injection classifier (GUARD-02).
  - Phase 4: pin temperature=0 + seed=42 here when eval mode is active.
"""
from __future__ import annotations

from openai import OpenAI

from dossier.core.settings import get_settings

# Locked model IDs (STACK.md §2.5; change requires CONTEXT.md decision event).
STRONG_MODEL_ID: str = "anthropic/claude-sonnet-4.5"
EMBEDDING_MODEL_ID: str = "text-embedding-3-small"
# 1536 dims matches source_chunks.embedding VECTOR(1536) in migration 0001.
# Accidental drift to a different dim is a runtime error at INSERT — Pitfall 2.2 defense.

CHEAP_MODEL_ID: str = "anthropic/claude-haiku-4.5"  # Reserved for Phase 3 per D-09.

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"  # CLAUDE.md lock.


def strong_model() -> OpenAI:
    """Return an openai.OpenAI client routed to OpenRouter.

    Callers use this for synthesis (D-03: openai.beta.chat.completions.parse
    against the Brief Pydantic schema). API key sourced from Settings — pydantic
    validation already raised at process start if it's missing, so no per-call
    strict-check needed.
    """
    api_key = get_settings().openrouter_api_key.get_secret_value()
    # base_url="https://openrouter.ai/api/v1" — CLAUDE.md / PLAT-03 lock.
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def embedding_model() -> str:
    """Return the embedding model id. Pair with embedding_client(), NOT strong_model().

    Embeddings go direct to api.openai.com — OpenRouter's /v1 surface is chat
    completions only and returns an empty data list for /v1/embeddings requests,
    which the openai SDK post-parser rejects with
    `ValueError: No embedding data received`. Use embedding_client() to get a
    client pointed at OpenAI's native endpoint.
    """
    return EMBEDDING_MODEL_ID


def embedding_client() -> OpenAI:
    """Return an openai.OpenAI client pointed at api.openai.com (no base_url override).

    Intentionally bypasses OpenRouter: OpenRouter proxies chat completions, not
    embeddings. Keeps chat/synth traffic on OpenRouter for unified billing while
    embeddings go direct.
    """
    api_key = get_settings().openai_api_key.get_secret_value()
    return OpenAI(api_key=api_key)  # default base_url = https://api.openai.com/v1


def cheap_model() -> str:
    """Return the cheap/classifier model id. Reserved for Phase 3 (D-09)."""
    return CHEAP_MODEL_ID


__all__ = [
    "CHEAP_MODEL_ID",
    "EMBEDDING_MODEL_ID",
    "OPENROUTER_BASE_URL",
    "STRONG_MODEL_ID",
    "cheap_model",
    "embedding_client",
    "embedding_model",
    "strong_model",
]
