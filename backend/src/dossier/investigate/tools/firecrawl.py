"""Firecrawl wrapper — deep-crawl the seed URL when Exa excerpts are thin.

Budget: ≤1 crawl per investigation (CONTEXT.md D-13). 500 free credits/mo must
stretch across Phase 2 dev + Phase 4 eval runs (10 companies × potential retries).

When to call: pipeline.py's discretion — CONTEXT.md §Claude's Discretion offers
heuristics (e.g., always crawl seed URL if input_type='url'; crawl if Exa returns
only one page for the seed domain). The wrapper itself is pure — it enforces the
budget but doesn't decide when to invoke.

Rejected alternatives:
  - Crawl every URL Exa returns: blows the 500-credit budget in 5 runs.
  - Skip Firecrawl entirely: Exa excerpts of JS-rendered landing pages are
    often just "Loading..." — Firecrawl is the escape hatch.
  - requests-html / BeautifulSoup: no JS rendering. Firecrawl is the SaaS for it.

Installed-API note (2026-04-22, firecrawl-py 4.22.3):
  `firecrawl.FirecrawlApp` is aliased to the v2 `firecrawl.Firecrawl` class.
  The v2 client uses `app.scrape(url, formats=[...], timeout=...)` (keyword args)
  and returns `firecrawl.v2.types.Document` — a Pydantic object with `.markdown`
  and `.metadata.title` attrs. The v1 `app.scrape_url(url, params={...})` and
  the dict-shaped response from earlier plans are obsolete here — the wrapper
  supports both attr-access AND dict-access for defensive/test-double safety.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from dossier.investigate.tools.types import ToolResult
from dossier.core.settings import get_settings

logger = logging.getLogger(__name__)

# Per-process budget: investigation_id -> count-already-consumed
_BUDGET: dict[str, int] = {}
_MAX_CRAWLS_PER_INVESTIGATION: int = 1  # D-13 budget
_FIRECRAWL_TIMEOUT_MS: int = 30_000  # 30s per ARCHITECTURE.md §10


def read_firecrawl_env(strict: bool = True) -> str:
    """Thin wrapper over Settings.firecrawl_api_key for backward compat."""
    key = get_settings().firecrawl_api_key.get_secret_value().strip()
    if strict and not key:
        raise RuntimeError(
            "FIRECRAWL_API_KEY not set. Copy .env.example to .env and paste key "
            "from https://www.firecrawl.dev/app/api-keys."
        )
    return key


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Firecrawl rejects non-http(s) URL: {url!r}")


def reset_budget_for_tests() -> None:
    """Testing hook: clear the per-process budget tracker."""
    _BUDGET.clear()


def _extract_markdown_and_title(result: Any) -> tuple[str, str | None]:
    """Pull (markdown, title) from either a Document object or a dict-shaped response.

    firecrawl-py v2 returns Document with .markdown and .metadata.title attrs.
    Older SDK versions + test doubles may return a dict like {"data": {"markdown": ..., "metadata": {"title": ...}}}.
    Normalize both shapes here.
    """
    # Dict path (legacy / test doubles)
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, dict):
            md = data.get("markdown", "") or ""
            meta = data.get("metadata") or {}
            title = meta.get("title") if isinstance(meta, dict) else None
            return md, title
        return "", None

    # Attr path (v2 Document)
    md = getattr(result, "markdown", "") or ""
    meta = getattr(result, "metadata", None)
    title = None
    if meta is not None:
        # metadata may itself be an object (DocumentMetadata) or a dict
        if isinstance(meta, dict):
            title = meta.get("title")
        else:
            title = getattr(meta, "title", None)
    return md, title


def crawl_seed_url(
    url: str,
    *,
    investigation_id: str,
    _budget_tracker: dict[str, int] | None = None,
) -> list[ToolResult]:
    """Crawl one URL via Firecrawl. Enforces ≤1 call per investigation_id (D-13).

    Returns [] if budget exhausted OR if the crawl errors (fail-open).
    Raises ValueError if url scheme is not http(s).
    """
    _validate_url(url)
    tracker = _budget_tracker if _budget_tracker is not None else _BUDGET

    used = tracker.get(investigation_id, 0)
    if used >= _MAX_CRAWLS_PER_INVESTIGATION:
        logger.warning(
            "Firecrawl budget exhausted for investigation %s (already %d crawl(s)); skipping %s",
            investigation_id,
            used,
            url,
        )
        return []

    try:
        from firecrawl import FirecrawlApp  # noqa: PLC0415
    except ImportError as exc:
        logger.warning("firecrawl-py not importable: %s", exc)
        return []

    try:
        app = FirecrawlApp(api_key=read_firecrawl_env(strict=True))
        # v2 API: scrape(url, formats=[...], timeout=ms) → Document
        result: Any = app.scrape(
            url,
            formats=["markdown"],
            timeout=_FIRECRAWL_TIMEOUT_MS,
        )
    except Exception as exc:  # noqa: BLE001 — firecrawl-py raises various types
        logger.warning("Firecrawl scrape failed for %s: %s", url, exc, exc_info=True)
        return []

    md, title = _extract_markdown_and_title(result)

    if not md.strip():
        logger.info("Firecrawl returned empty markdown for %s (fail-open)", url)
        return []

    # Budget consumed.
    tracker[investigation_id] = used + 1

    return [
        ToolResult(
            url=url,
            source_kind="crawl",
            text=md,
            title=title,
            raw_metadata={"crawl_provider": "firecrawl"},
        )
    ]


__all__ = ["crawl_seed_url", "read_firecrawl_env", "reset_budget_for_tests"]
