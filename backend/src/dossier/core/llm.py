"""LLM and embedding clients + call-shape helpers.

Two layers:

1. Client factories (`strong_model`, `embedding_client`) — the raw OpenAI
   SDK objects. Test code monkeypatches these to inject mocks. Chat
   completions go through OpenRouter via base_url override; embeddings
   go direct to api.openai.com (OpenRouter doesn't proxy /v1/embeddings).

2. Call helpers (`structured_call`, `completion`, `embed`) — the small
   surface every caller actually wants. They hide the
   `.beta.chat.completions.parse(...)` boilerplate, the parsed=None /
   refusal fallback path, the model-id resolution, and the embeddings
   batching. Each accepts `client=` for test injection so today's mock
   pattern keeps working.

Why a `Literal["strong","cheap"]` alias instead of raw model strings:
swapping Sonnet → Opus for one node should be a one-line change, not a
search-and-replace across constants. The alias decouples *role* from
*model id* the way the rest of the code already decouples adapter from
implementation.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from dossier.core.settings import get_settings

STRONG_MODEL_ID: str = "anthropic/claude-sonnet-4.5"
CHEAP_MODEL_ID: str = "anthropic/claude-haiku-4.5"

# 1536 dims must match source_chunks.embedding VECTOR(1536); changing the
# model means a migration.
EMBEDDING_MODEL_ID: str = "text-embedding-3-small"

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

ModelAlias = Literal["strong", "cheap"]
DEFAULT_EMBED_BATCH_SIZE: int = 100

_S = TypeVar("_S", bound=BaseModel)


# ---------------------------------------------------------------------------
# Raw client factories — preserved so tests can monkeypatch directly
# ---------------------------------------------------------------------------


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


def _resolve_model(alias: ModelAlias) -> str:
    return STRONG_MODEL_ID if alias == "strong" else CHEAP_MODEL_ID


# ---------------------------------------------------------------------------
# Call helpers — the small surface every caller wants
# ---------------------------------------------------------------------------


def structured_call_with_status(
    schema: type[_S],
    *,
    messages: list[dict[str, Any]],
    model: ModelAlias = "strong",
    client: Any | None = None,
    **kwargs: Any,
) -> tuple[_S | None, str | None]:
    """Lower-level structured call. Returns (parsed, refusal).

    Use when an operator needs to distinguish "model refused" from "model
    returned malformed JSON" — synthesize.py raises different PipelineError
    messages for the two so an on-call can read the trace and act. Most
    callers want `structured_call` (which collapses both to None).
    """
    active_client = client if client is not None else strong_model()
    response = active_client.beta.chat.completions.parse(
        model=_resolve_model(model),
        messages=messages,
        response_format=schema,
        **kwargs,
    )
    message = response.choices[0].message
    refusal = getattr(message, "refusal", None)
    if refusal:
        return None, refusal
    return getattr(message, "parsed", None), None


def structured_call(
    schema: type[_S],
    *,
    messages: list[dict[str, Any]],
    model: ModelAlias = "strong",
    client: Any | None = None,
    **kwargs: Any,
) -> _S | None:
    """Pydantic-validated structured output. Returns parsed instance or None.

    None is returned for both refusal and parsed=None. The caller decides
    the fallback (raise, fail-open with a default, log + return empty);
    collapsing the two states keeps the helper tiny and the call sites
    explicit about what failure means in their context.

    `model` is "strong" or "cheap" — see ModelAlias. `**kwargs` flow to
    `.beta.chat.completions.parse` (e.g. temperature, max_tokens).
    """
    parsed, _refusal = structured_call_with_status(
        schema, messages=messages, model=model, client=client, **kwargs
    )
    return parsed


def completion(
    *,
    messages: list[dict[str, Any]],
    model: ModelAlias = "strong",
    client: Any | None = None,
    **kwargs: Any,
) -> Any:
    """Plain (non-structured) chat completion. Returns the raw response so
    the caller can inspect `.choices[0].message.content` / token usage.
    """
    active_client = client if client is not None else strong_model()
    return active_client.chat.completions.create(
        model=_resolve_model(model),
        messages=messages,
        **kwargs,
    )


def embed(
    texts: Iterable[str],
    *,
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
    client: Any | None = None,
) -> list[list[float]]:
    """Batched embeddings via OpenAI direct (OpenRouter doesn't proxy here).

    Returns a list whose order matches `texts`. Empty input → empty output.
    """
    items = list(texts)
    if not items:
        return []
    active = client if client is not None else embedding_client()
    out: list[list[float]] = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        resp = active.embeddings.create(model=EMBEDDING_MODEL_ID, input=batch)
        out.extend(d.embedding for d in resp.data)
    return out


__all__ = [
    "CHEAP_MODEL_ID",
    "DEFAULT_EMBED_BATCH_SIZE",
    "EMBEDDING_MODEL_ID",
    "ModelAlias",
    "OPENROUTER_BASE_URL",
    "STRONG_MODEL_ID",
    "cheap_model",
    "completion",
    "embed",
    "embedding_client",
    "embedding_model",
    "strong_model",
    "structured_call",
    "structured_call_with_status",
]
