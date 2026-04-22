"""Exa search wrapper — primary web search for Phase 2 (CONTEXT.md D-11).

Exa was chosen over Tavily per CLAUDE.md locked decision: Tavily hides source
spans, Exa returns excerpts that downstream grounding needs. See STACK.md §2.4.

Fail-open policy (CONTEXT.md D-09 §Claude's Discretion):
  - If Exa returns zero results, return []. Pipeline emits a thin brief with a
    logged warning rather than marking the investigation failed. Partial > none.
  - If the Exa API itself errors (network, 5xx), retry 3x via tenacity then raise;
    pipeline catches and marks investigation failed with error set.

Rejected alternatives:
  - Tavily: hides source spans (CLAUDE.md lock).
  - Async client (AsyncExa): Phase 2 pipeline is sequential per D-04; adds asyncio
    complexity for no throughput benefit with 3 sequential tool calls.
  - No retry: Exa free tier has daily caps AND transient 502s — unretried calls
    fail more often than retried ones.

Installed-API note (2026-04-22):
  exa-py on this env exposes `search_and_contents(query, **kwargs)` but marks it
  DEPRECATED in favor of `search(query, contents={"text": True})`. We use
  search_and_contents for now because the test monkeypatches it — migrating to
  `search(contents=...)` is a one-liner swap when exa-py removes the deprecated
  method.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dossier.investigate.tools.types import ToolResult
from dossier.observability import load_env

logger = logging.getLogger(__name__)


def read_exa_env(strict: bool = True) -> str:
    load_env()
    key = os.environ.get("EXA_API_KEY", "").strip()
    if strict and not key:
        raise RuntimeError(
            "EXA_API_KEY not set. Copy .env.example to .env and paste key from "
            "https://dashboard.exa.ai/api-keys."
        )
    return key


class ExaSearchError(RuntimeError):
    """Raised on non-recoverable Exa failures after retries are exhausted."""


def _extract_field(result: Any, name: str, default: Any = None) -> Any:
    """Pull a field from an Exa result — works for both dict-shaped and object-shaped results."""
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
    """Call Exa search_and_contents; raise ExaSearchError on transient failure.

    This is the retry boundary — callers see either a list or a raised
    ExaSearchError after 3 attempts.
    """
    from exa_py import Exa  # noqa: PLC0415  (deferred import — allows tests to patch)

    client = Exa(api_key=read_exa_env(strict=True))
    try:
        # exa-py: use search_and_contents to get url + text in one call.
        # Deprecation note in module docstring.
        response = client.search_and_contents(
            query,
            num_results=num_results,
            text=True,
            use_autoprompt=True,
        )
    except Exception as exc:  # noqa: BLE001 — exa-py raises various types
        raise ExaSearchError(f"Exa search failed: {exc}") from exc

    # exa-py returns an object with .results list; each item has .url/.text/.title/.score.
    # Defensive: some versions wrap differently.
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
    """Run an Exa web search; return up to `num_results` ToolResult objects.

    Fail-open: returns [] on empty results. Raises ExaSearchError after retries
    if the Exa API is unreachable/errored; pipeline.py catches and marks the
    investigation failed.
    """
    try:
        raw_results = _exa_call(query, num_results)
    except ExaSearchError:
        logger.warning("Exa search exhausted retries for query: %r", query, exc_info=True)
        raise

    if not raw_results:
        logger.info("Exa search returned 0 results for query: %r (fail-open)", query)
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
        if r.get("url")  # skip rows with no URL — cannot cite without it
    ]


__all__ = ["ExaSearchError", "read_exa_env", "search"]
