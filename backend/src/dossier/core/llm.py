from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterable, Literal, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from dossier.core.settings import get_settings

logger = logging.getLogger(__name__)

STRONG_MODEL_ID: str = "anthropic/claude-sonnet-4.5"
CHEAP_MODEL_ID: str = "anthropic/claude-haiku-4.5"

# 1536 dims must match source_chunks.embedding VECTOR(1536); changing the model means a migration.
EMBEDDING_MODEL_ID: str = "text-embedding-3-small"

OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

ModelAlias = Literal["strong", "cheap"]
DEFAULT_EMBED_BATCH_SIZE: int = 100

_S = TypeVar("_S", bound=BaseModel)


def strong_model() -> OpenAI:
    api_key = get_settings().openrouter_api_key.get_secret_value()
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)


def embedding_client() -> OpenAI:
    # Embeddings go direct to api.openai.com — OpenRouter doesn't proxy /v1/embeddings.
    api_key = get_settings().openai_api_key.get_secret_value()
    return OpenAI(api_key=api_key)


def _resolve_model(alias: ModelAlias) -> str:
    return STRONG_MODEL_ID if alias == "strong" else CHEAP_MODEL_ID


def structured_call_with_status(
    schema: type[_S],
    *,
    messages: list[dict[str, Any]],
    model: ModelAlias = "strong",
    client: Any | None = None,
    **kwargs: Any,
) -> tuple[_S | None, str | None]:
    active_client = client if client is not None else strong_model()
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
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
        except json.JSONDecodeError as exc:
            last_exc = exc
            delay = 2 ** attempt  # 1s, 2s
            logger.warning(
                "structured_call: JSONDecodeError on attempt %d/%d — OpenRouter returned "
                "non-JSON body; retrying in %ds. Error: %s",
                attempt + 1, 3, delay, exc,
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def structured_call(
    schema: type[_S],
    *,
    messages: list[dict[str, Any]],
    model: ModelAlias = "strong",
    client: Any | None = None,
    **kwargs: Any,
) -> _S | None:
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

