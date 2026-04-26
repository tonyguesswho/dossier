"""Exa web search wrapper.

Chosen over Tavily because Exa returns excerpts that downstream citation
grounding can substring-match against. Tavily collapses to summaries.

Fail-open: empty results return []. Network/5xx errors retry 3x then raise
ExaSearchError; the caller decides whether to fail the investigation.

Note: exa-py exposes both `search_and_contents` and a newer `search(contents=...)`.
We use the older entry point because the unit tests monkeypatch it; swap is
a one-liner if exa-py drops it.
"""
from __future__ import annotations

import logging
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dossier.investigate.tools.types import ToolResult
from dossier.core.settings import get_settings

logger = logging.getLogger(__name__)


def read_exa_env(strict: bool = True) -> str:
    key = get_settings().exa_api_key.get_secret_value().strip()
    if strict and not key:
        raise RuntimeError("EXA_API_KEY not set")
    return key


class ExaSearchError(RuntimeError):
    """Raised after retries exhaust on a non-recoverable Exa failure."""


def _extract_field(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, dict):
        return result.get(name, default)
    return getattr(result, name, default)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type((ExaSearchError,)),
    reraise=True,
)
def _exa_call(query: str, num_results: int) -> list[dict[str, Any]]:
    from exa_py import Exa  # noqa: PLC0415  (deferred so tests can patch)

    client = Exa(api_key=read_exa_env(strict=True))
    try:
        response = client.search_and_contents(
            query,
            num_results=num_results,
            text=True,
            type="deep",
        )
    except Exception as exc:  # noqa: BLE001 — exa-py raises various types
        raise ExaSearchError(f"Exa search failed: {exc}") from exc

    results = getattr(response, "results", None)
    if results is None:
        results = response if isinstance(response, list) else []

    return [
        {
            "url": _extract_field(r, "url", "") or "",
            "text": _extract_field(r, "text", "") or "",
            "title": _extract_field(r, "title", None),
            "score": _extract_field(r, "score", None),
        }
        for r in results
    ]


def search(query: str, *, num_results: int = 8) -> list[ToolResult]:
    """Run an Exa web search; return up to `num_results` ToolResult objects."""
    try:
        raw_results = _exa_call(query, num_results)
    except ExaSearchError:
        logger.warning("Exa search exhausted retries for query: %r", query, exc_info=True)
        raise

    if not raw_results:
        logger.info("Exa search returned 0 results for query: %r", query)
        return []

    return [
        ToolResult(
            url=r["url"],
            source_kind="web",
            text=r["text"] or "",
            title=r.get("title"),
            raw_metadata={"score": r["score"]} if r.get("score") is not None else {},
        )
        for r in raw_results
        if r.get("url")  # can't cite without a URL
    ]


__all__ = ["ExaSearchError", "read_exa_env", "search"]
