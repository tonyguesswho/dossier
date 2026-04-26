"""Two-stage fan-out via langgraph.types.Send.

Stage 1: Exa + NewsAPI + Firecrawl (when input_url is present).
After Stage 1: Haiku founder extraction.
Stage 2: GitHub-per-founder + Crunchbase.

NewsAPI + Crunchbase wrappers are native async. Exa + GitHub + Firecrawl
are sync — wrapped via asyncio.to_thread so the graph stays non-blocking.
Tool nodes fail-open: any error logs and returns no chunks.

Raw source text never enters graph state. Tool nodes register ToolResult
objects in a module-level cache (ingest_and_embed drains it); state only
carries chunk references.

Every tool node is the same shape: invoke, fail-open with `[]`, cache
results, build chunk refs, append to state. `_run_tool_node` is that
template — each node function below is the per-tool *specification*
(label, section hint, invocation) without re-stating the envelope.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from langgraph.types import Send

from dossier.investigate.tools.crunchbase import search as crunchbase_search
from dossier.investigate.tools.newsapi import search as newsapi_search
from dossier.investigate.tools.types import ToolResult

from ..state_accessors import append_retrieved_chunks

from ..state import DossierState, RetrievedChunkRef
from .ingest_and_embed import cache_tool_result

logger = logging.getLogger(__name__)


def _cache_results(investigation_id: str, results: list[ToolResult]) -> None:
    for result in results:
        cache_tool_result(investigation_id, result)


def _tool_results_to_chunk_refs(
    results: list[ToolResult], section_hint: str = "general"
) -> list[RetrievedChunkRef]:
    """Build chunk refs without text. ingest_and_embed fills real ids/offsets
    after the DB insert; char_end=len(text) here is just a payload-size hint.
    """
    refs: list[RetrievedChunkRef] = []
    for r in results:
        refs.append(
            RetrievedChunkRef(
                chunk_id="",
                source_id="",
                url=r.url,
                source_kind=r.source_kind,
                char_start=0,
                char_end=len(r.text),
                section_hint=section_hint,
            )
        )
    return refs


async def _run_tool_node(
    state: DossierState,
    *,
    label: str,
    section_hint: str,
    invoke: Callable[[], Awaitable[list[ToolResult]]],
) -> dict:
    """Standard tool-node envelope: invoke, fail-open, cache, build refs, append.

    `invoke` is a zero-arg coroutine factory so the per-tool argument-prep
    stays at the call site. Any exception is logged + swallowed; the run
    continues with the chunks other tools produced.
    """
    try:
        results = await invoke()
    except Exception:  # noqa: BLE001 — fail-open is the contract
        logger.warning("run_%s: error", label, exc_info=True)
        results = []
    _cache_results(state["investigation_id"], results)
    refs = _tool_results_to_chunk_refs(results, section_hint=section_hint)
    logger.info("run_%s: got %d results", label, len(results))
    return append_retrieved_chunks(refs)


async def run(state: DossierState) -> dict:
    """Orchestration node — no state updates."""
    logger.info("gather_fanout: routing stage1 for company=%s", state["company"])
    return {}


def stage1_router(state: DossierState) -> list[Send]:
    """Exa + NewsAPI (+ Firecrawl if input_url). Deck-input investigations
    skip web tools — the deck text is already the corpus.
    """
    if state.get("input_type") == "deck":
        logger.info("gather_fanout: skipping stage1 for deck-input investigation")
        return []
    sends = [
        Send("exa_search", state),
        Send("newsapi_search", state),
    ]
    if state.get("input_url"):
        sends.append(Send("firecrawl_crawl", state))
    return sends


def stage2_router(state: DossierState) -> list[Send]:
    if state.get("input_type") == "deck":
        logger.info("gather_fanout: skipping stage2 for deck-input investigation")
        return []
    sends = [Send("crunchbase_search", state)]
    for founder in state.get("founder_candidates", [])[:5]:
        sends.append(Send("github_founder", {**state, "current_founder": founder}))
    return sends


async def run_exa(state: DossierState) -> dict:
    from dossier.investigate.tools.exa import search as exa_search

    company = state["company"]
    context_hint = state.get("context_hint")
    targeted = state.get("targeted_sections") or []

    query = company
    if targeted:
        query = f"{company} {' '.join(targeted)}"
    if context_hint:
        query = f"{query} {context_hint}"

    return await _run_tool_node(
        state,
        label="exa",
        section_hint="general",
        invoke=lambda: asyncio.to_thread(exa_search, query),
    )


async def run_newsapi(state: DossierState) -> dict:
    company = state["company"]
    context_hint = state.get("context_hint")
    return await _run_tool_node(
        state,
        label="newsapi",
        section_hint="general",
        invoke=lambda: newsapi_search(company=company, context_hint=context_hint),
    )


async def run_firecrawl(state: DossierState) -> dict:
    """Firecrawl deep-crawl, only when input_url is set."""
    input_url = state.get("input_url")
    if not input_url:
        return append_retrieved_chunks([])

    from dossier.investigate.tools.firecrawl import crawl_seed_url

    investigation_id = state["investigation_id"]
    return await _run_tool_node(
        state,
        label="firecrawl",
        section_hint="general",
        invoke=lambda: asyncio.to_thread(
            crawl_seed_url, input_url, investigation_id=investigation_id
        ),
    )


async def run_github_founder(state: DossierState) -> dict:
    """Per-founder GitHub search; current_founder injected by stage2_router."""
    founder = state.get("current_founder", "")
    if not founder:
        return append_retrieved_chunks([])

    from dossier.investigate.tools.github import fetch_founder_profile

    return await _run_tool_node(
        state,
        label="github_founder",
        section_hint="founders",
        invoke=lambda: asyncio.to_thread(fetch_founder_profile, founder),
    )


async def run_crunchbase(state: DossierState) -> dict:
    company = state["company"]
    return await _run_tool_node(
        state,
        label="crunchbase",
        section_hint="company",
        invoke=lambda: crunchbase_search(company=company),
    )


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
