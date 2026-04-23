"""NewsAPI search wrapper for Phase 3 Stage-1 fan-out (D-04).

NewsAPI is the primary press/news source for the investigation graph. ToS note:
the NewsAPI free tier is dev-only — STATE.md blockers record the plan to
substitute GDELT or Bing News in production. This wrapper fail-opens (returns
[]) when NEWSAPI_API_KEY is unset, so local dev without the key still runs
(missing secondary-source tools = thin brief, not a failed investigation per
D-09 §Claude's Discretion).

Implementation notes:
  - Uses httpx (already a transitive dep via openai-python / firecrawl-py).
    No new SDK imports. Deferred import inside function body so unit tests can
    monkeypatch httpx.AsyncClient without reloading the wrapper module (mirrors
    exa.py's `from exa_py import Exa` pattern).
  - tenacity @retry(stop_after_attempt=3) matches the exa.py retry envelope:
    3 attempts, exponential backoff, reraise on exhaustion.
  - Fail-open policy: any exception after retries → log.warning + return [].
    Uniform with exa.py / github.py (D-09 fail-open: a failing secondary
    source never kills an investigation).

Rate limits (free tier): 100 requests/day. Phase 3 budget: one call per
investigation (no pagination) keeps us well inside the ceiling even with
eval-set reruns.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from tenacity import retry, stop_after_attempt, wait_exponential

from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)

NEWSAPI_BASE_URL = "https://newsapi.org/v2"
_PAGE_SIZE = 10  # 10 articles/investigation — stays well inside 100 req/day free tier


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _fetch_articles(query: str, api_key: str) -> list[dict]:
    """Fetch top articles from NewsAPI /v2/everything. Retried up to 3 times.

    Deferred import of httpx so tests can monkeypatch httpx.AsyncClient.
    """
    import httpx  # noqa: PLC0415 — deferred for monkeypatch safety

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
    """Search NewsAPI for recent press mentions of `company`.

    Args:
        company: Company name or search string.
        context_hint: Optional free-form context appended to the query.

    Returns:
        list[ToolResult] with source_kind='news'. Empty list on any failure
        (missing API key, network error after retries, empty response body).
    """
    api_key = os.environ.get("NEWSAPI_API_KEY")
    if not api_key:
        logger.warning("newsapi.search: NEWSAPI_API_KEY not set — skipping news search")
        return []

    query = company if not context_hint else f"{company} {context_hint}"

    try:
        articles = await _fetch_articles(query, api_key)
    except Exception:  # noqa: BLE001 — fail-open on any transient failure after retries
        logger.warning(
            "newsapi.search: failed after retries for query=%r", query, exc_info=True
        )
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

        # Combine title + description + content for the text field (content may be
        # truncated by NewsAPI's free tier — description fills the gap).
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
