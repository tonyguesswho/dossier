"""LLM and embedding client factories.

Chat completions go through OpenRouter via the stock openai SDK with a
base_url override. Embeddings go direct to api.openai.com because
OpenRouter's /v1 surface doesn't proxy /v1/embeddings.
"""
from __future__ import annotations

from openai import OpenAI

from dossier.core.settings import get_settings

STRONG_MODEL_ID: str = "anthropic/claude-sonnet-4.5"
CHEAP_MODEL_ID: str = "anthropic/claude-haiku-4.5"

# 1536 dims must match source_chunks.embedding VECTOR(1536); changing the
# model means a migration.
EMBEDDING_MODEL_ID: str = "text-embedding-3-small"

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"


def strong_model() -> OpenAI:
    """OpenAI client routed to OpenRouter, used for synthesis."""
    api_key = get_settings().openrouter_api_key.get_secret_value()
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def cheap_model() -> str:
    """Model id for the classifier / lightweight calls."""
    return CHEAP_MODEL_ID


def embedding_model() -> str:
    """Model id for embeddings. Pair with embedding_client(), not strong_model()."""
    return EMBEDDING_MODEL_ID


def embedding_client() -> OpenAI:
    """OpenAI client pointed at api.openai.com (no base_url override)."""
    api_key = get_settings().openai_api_key.get_secret_value()
    return OpenAI(api_key=api_key)


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
