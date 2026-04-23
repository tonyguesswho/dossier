"""Two-stage fan-out orchestration via langgraph.types.Send (D-04, 03-CONTEXT.md).

Stage 1: Exa + NewsAPI + Firecrawl (if input_url present) — parallel Send.
  After Stage 1 Exa completes: Haiku 4.5 founder extraction (Plan 03-05).
Stage 2: GitHub-per-founder + Crunchbase — parallel Send.

Tool wrappers follow fail-open policy (log warning + return [] on error).
NewsAPI + Crunchbase wrappers (Plan 03-04) are natively async; Exa + GitHub
+ Firecrawl wrappers (Phase 2) remain synchronous — they are invoked via
`asyncio.to_thread()` here so graph node coroutines stay non-blocking.

Deferred SDK imports inside the sync wrapper modules enable monkeypatching in
tests without module reload.
"""
from __future__ import annotations

import asyncio
import logging

from langgraph.types import Send

from dossier.investigate.tools.crunchbase import search as crunchbase_search
from dossier.investigate.tools.newsapi import search as newsapi_search
from dossier.investigate.tools.types import ToolResult

from ..state import DossierState, RetrievedChunkRef
from .ingest_and_embed import cache_tool_result

logger = logging.getLogger(__name__)


def _cache_results(investigation_id: str, results: list[ToolResult]) -> None:
    """Register every ToolResult with ingest_and_embed's cache.

    State cannot carry raw source text (ARCHITECTURE.md §9 anti-pattern), so
    each tool node hands its ToolResult objects off to a module-level cache
    keyed by investigation_id. ingest_and_embed.run() drains the cache once
    the Stage-2 fan-out has converged. Per-url overwrite means a re-gather
    pass cleanly supersedes the previous fetch without duplicating state.
    """
    for result in results:
        cache_tool_result(investigation_id, result)


def _tool_results_to_chunk_refs(
    results: list[ToolResult], section_hint: str = "general"
) -> list[RetrievedChunkRef]:
    """Convert raw ToolResult list → RetrievedChunkRef list for state accumulation.

    chunk_id and source_id are empty placeholders here — ingest_and_embed fills
    them after DB insert. text is deliberately NOT included in the ref per
    ARCHITECTURE.md §9 anti-pattern (no raw text in graph state). char_end=len(text)
    temporarily encodes the payload size; ingest_and_embed recomputes real offsets
    from the chunked source rows.
    """
    refs: list[RetrievedChunkRef] = []
    for r in results:
        refs.append(
            RetrievedChunkRef(
                chunk_id="",  # filled by ingest_and_embed after DB write
                source_id="",  # filled by ingest_and_embed after DB write
                url=r.url,
                source_kind=r.source_kind,
                char_start=0,
                char_end=len(r.text),
                section_hint=section_hint,
            )
        )
    return refs


async def run(state: DossierState) -> dict:
    """Orchestration node — not a tool-calling node. Returns no state updates."""
    logger.info("gather_fanout: routing stage1 for company=%s", state["company"])
    return {}


def stage1_router(state: DossierState) -> list[Send]:
    """Route parallel Stage-1 branches: Exa + NewsAPI + Firecrawl (if URL)."""
    sends = [
        Send("exa_search", state),
        Send("newsapi_search", state),
    ]
    if state.get("input_url"):
        sends.append(Send("firecrawl_crawl", state))
    return sends


def stage2_router(state: DossierState) -> list[Send]:
    """Route Stage-2 branches after founder extraction: GitHub × N + Crunchbase."""
    sends = [Send("crunchbase_search", state)]
    for founder in state.get("founder_candidates", [])[:5]:  # D-05: cap at 5
        sends.append(Send("github_founder", {**state, "current_founder": founder}))
    return sends


# --- Tool nodes ---


async def run_exa(state: DossierState) -> dict:
    """Exa Stage-1 search. Uses existing sync wrapper via asyncio.to_thread."""
    # Deferred import: keeps this module importable in envs where exa_py isn't
    # available (e.g. api-lambda image), and preserves test monkeypatch ability.
    from dossier.investigate.tools.exa import ExaSearchError
    from dossier.investigate.tools.exa import search as exa_search

    company = state["company"]
    context_hint = state.get("context_hint")
    targeted = state.get("targeted_sections") or []

    query = company
    if targeted:
        query = f"{company} {' '.join(targeted)}"
    if context_hint:
        query = f"{query} {context_hint}"

    try:
        results = await asyncio.to_thread(exa_search, query)
    except ExaSearchError:
        # exa.search raises after 3 retries — graph node fails open so Stage 1
        # still emits NewsAPI + Firecrawl results (partial brief > failed investigation).
        logger.warning("run_exa: exa.search exhausted retries for %r", query, exc_info=True)
        results = []
    except Exception:  # noqa: BLE001 — defensive: never kill the investigation on Exa outage
        logger.warning("run_exa: unexpected exa.search error for %r", query, exc_info=True)
        results = []

    _cache_results(state["investigation_id"], results)
    refs = _tool_results_to_chunk_refs(results, section_hint="general")
    logger.info("run_exa: company=%s got %d results", company, len(results))
    return {"retrieved_chunks": refs}


async def run_newsapi(state: DossierState) -> dict:
    """NewsAPI press search for company. Native async (Plan 03-04)."""
    company = state["company"]
    context_hint = state.get("context_hint")
    results = await newsapi_search(company=company, context_hint=context_hint)
    _cache_results(state["investigation_id"], results)
    refs = _tool_results_to_chunk_refs(results, section_hint="general")
    logger.info("run_newsapi: company=%s got %d results", company, len(results))
    return {"retrieved_chunks": refs}


async def run_firecrawl(state: DossierState) -> dict:
    """Firecrawl deep-crawl of seed URL (only when input_url present).

    The Firecrawl wrapper is sync and enforces a per-investigation budget
    (D-13: ≤1 crawl/investigation); run it on a thread to stay non-blocking.
    """
    input_url = state.get("input_url")
    if not input_url:
        return {"retrieved_chunks": []}

    from dossier.investigate.tools.firecrawl import crawl_seed_url

    investigation_id = state["investigation_id"]
    try:
        results = await asyncio.to_thread(
            crawl_seed_url, input_url, investigation_id=investigation_id
        )
    except Exception:  # noqa: BLE001 — fail-open on any firecrawl-py error
        logger.warning("run_firecrawl: crawl_seed_url error for url=%s", input_url, exc_info=True)
        results = []

    _cache_results(state["investigation_id"], results)
    refs = _tool_results_to_chunk_refs(results, section_hint="general")
    logger.info("run_firecrawl: url=%s got %d results", input_url, len(results))
    return {"retrieved_chunks": refs}


async def run_github_founder(state: DossierState) -> dict:
    """Per-founder GitHub search. current_founder injected by stage2_router."""
    founder = state.get("current_founder", "")
    if not founder:
        return {"retrieved_chunks": []}

    from dossier.investigate.tools.github import fetch_founder_profile

    try:
        results = await asyncio.to_thread(fetch_founder_profile, founder)
    except Exception:  # noqa: BLE001 — fail-open; GitHub outage must not kill graph
        logger.warning(
            "run_github_founder: fetch_founder_profile error for founder=%r",
            founder,
            exc_info=True,
        )
        results = []

    _cache_results(state["investigation_id"], results)
    refs = _tool_results_to_chunk_refs(results, section_hint="founders")
    logger.info("run_github_founder: founder=%r got %d results", founder, len(results))
    return {"retrieved_chunks": refs}


async def run_crunchbase(state: DossierState) -> dict:
    """Crunchbase org enrichment. Native async (Plan 03-04)."""
    company = state["company"]
    results = await crunchbase_search(company=company)
    _cache_results(state["investigation_id"], results)
    refs = _tool_results_to_chunk_refs(results, section_hint="company")
    logger.info("run_crunchbase: company=%s got %d results", company, len(results))
    return {"retrieved_chunks": refs}


__all__ = [
    "run",
    "run_crunchbase",
    "run_exa",
    "run_firecrawl",
    "run_github_founder",
    "run_newsapi",
    "stage1_router",
    "stage2_router",
]
