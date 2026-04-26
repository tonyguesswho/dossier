"""NewsAPI search wrapper for press mentions.

Free tier is dev-only per NewsAPI ToS — production should swap for GDELT or
Bing News. Fail-open: missing key, network error, or empty body all return [],
keeping a missing secondary source from killing an investigation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from tenacity import retry, stop_after_attempt, wait_exponential

from dossier.core.settings import get_settings
from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)

NEWSAPI_BASE_URL = "https://newsapi.org/v2"
_PAGE_SIZE = 10  # one call/investigation stays well inside 100 req/day


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _fetch_articles(query: str, api_key: str) -> list[dict]:
    import httpx  # noqa: PLC0415 — deferred so tests can patch httpx.AsyncClient

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{NEWSAPI_BASE_URL}/everything",
            params={
                "q": query,
                "language": "en",
                "sortBy": "relevancy",
                "pageSize": _PAGE_SIZE,
                "apiKey": api_key,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("articles", [])


async def search(company: str, context_hint: str | None = None) -> list[ToolResult]:
    """Search NewsAPI for recent press mentions of `company`."""
    api_key = get_settings().newsapi_api_key
    if not api_key:
        logger.warning("newsapi.search: NEWSAPI_API_KEY not set — skipping")
        return []

    query = company if not context_hint else f"{company} {context_hint}"

    try:
        articles = await _fetch_articles(query, api_key)
    except Exception:  # noqa: BLE001 — fail-open after retries
        logger.warning("newsapi.search: failed for query=%r", query, exc_info=True)
        return []

    results: list[ToolResult] = []
    for article in articles:
        url = article.get("url", "") or ""
        title = article.get("title", "") or ""
        description = article.get("description") or ""
        content = article.get("content") or ""
        published_at = article.get("publishedAt", "")
        source_obj = article.get("source") or {}
        source_name = (
            source_obj.get("name") if isinstance(source_obj, dict) else None
        ) or "NewsAPI"

        # Free-tier `content` is truncated; description fills the gap.
        text = "\n\n".join(p for p in (title, description, content) if p)
        if not text.strip() or not url:
            continue

        results.append(
            ToolResult(
                url=url,
                source_kind="news",
                text=text,
                title=title or None,
                fetched_at=datetime.now(timezone.utc),
                raw_metadata={
                    "published_at": published_at,
                    "source_name": source_name,
                },
            )
        )

    logger.info("newsapi.search: query=%r returned %d articles", query, len(results))
    return results


__all__ = ["NEWSAPI_BASE_URL", "search"]
