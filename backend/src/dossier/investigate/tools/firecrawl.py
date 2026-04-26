"""Firecrawl wrapper for deep-crawling a seed URL when Exa excerpts are thin.

Budget: ≤1 crawl per investigation — 500 free credits/mo has to stretch
across dev, eval reruns, and the demo. The wrapper enforces the budget;
when to invoke is the caller's call.

firecrawl-py 4.x exposes `app.scrape(url, formats=[...], timeout=ms)` returning
a Document with `.markdown` and `.metadata.title`. Older SDKs and test doubles
hand back a dict — we accept both shapes.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from dossier.investigate.tools.types import ToolResult
from dossier.core.settings import get_settings

logger = logging.getLogger(__name__)

_BUDGET: dict[str, int] = {}
_MAX_CRAWLS_PER_INVESTIGATION: int = 1
_FIRECRAWL_TIMEOUT_MS: int = 30_000


def read_firecrawl_env(strict: bool = True) -> str:
    key = get_settings().firecrawl_api_key.get_secret_value().strip()
    if strict and not key:
        raise RuntimeError("FIRECRAWL_API_KEY not set")
    return key


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Firecrawl rejects non-http(s) URL: {url!r}")


def reset_budget_for_tests() -> None:
    _BUDGET.clear()


def _extract_markdown_and_title(result: Any) -> tuple[str, str | None]:
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, dict):
            md = data.get("markdown", "") or ""
            meta = data.get("metadata") or {}
            title = meta.get("title") if isinstance(meta, dict) else None
            return md, title
        return "", None

    md = getattr(result, "markdown", "") or ""
    meta = getattr(result, "metadata", None)
    title = None
    if meta is not None:
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
    """Crawl one URL; enforces ≤1 call per investigation_id.

    Fail-open on budget exhaustion or crawl errors. Raises ValueError only
    on non-http(s) URLs (a programmer error, not a runtime condition).
    """
    _validate_url(url)
    tracker = _budget_tracker if _budget_tracker is not None else _BUDGET

    used = tracker.get(investigation_id, 0)
    if used >= _MAX_CRAWLS_PER_INVESTIGATION:
        logger.warning(
            "Firecrawl budget exhausted for investigation %s (already %d crawl(s)); skipping %s",
            investigation_id, used, url,
        )
        return []

    try:
        from firecrawl import FirecrawlApp  # noqa: PLC0415
    except ImportError as exc:
        logger.warning("firecrawl-py not importable: %s", exc)
        return []

    try:
        app = FirecrawlApp(api_key=read_firecrawl_env(strict=True))
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
        logger.info("Firecrawl returned empty markdown for %s", url)
        return []

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
