from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from langgraph.types import Send

from dossier.investigate.tools.crunchbase import search as crunchbase_search
from dossier.investigate.tools.newsapi import search as newsapi_search
from dossier.investigate.tools.types import ToolResult

from ..state_accessors import append_staged_sources

from ..state import DossierState, StagedSourceRef

logger = logging.getLogger(__name__)


def _tool_results_to_staged_sources(
    results: list[ToolResult],
    *,
    section_hint: str = "general",
    pass_index: int,
) -> list[StagedSourceRef]:
    refs: list[StagedSourceRef] = []
    for r in results:
        refs.append(
            StagedSourceRef(
                url=r.url,
                source_kind=r.source_kind,
                text=r.text,
                title=r.title,
                fetched_at=r.fetched_at,
                raw_metadata=r.raw_metadata,
                section_hint=section_hint,
                pass_index=pass_index,
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
    try:
        results = await invoke()
    except Exception:  # noqa: BLE001 — fail-open is the contract
        logger.warning("run_%s: error", label, exc_info=True)
        results = []
    refs = _tool_results_to_staged_sources(
        results,
        section_hint=section_hint,
        pass_index=state.get("reflection_count", 0),
    )
    logger.info("run_%s: got %d results", label, len(results))
    return append_staged_sources(refs)


async def run(state: DossierState) -> dict:
    logger.info("gather_fanout: routing stage1 for company=%s", state["company"])
    return {}


def stage1_router(state: DossierState) -> list[Send]:
    # Deck-input investigations skip web tools — the deck text is already the corpus.
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
    input_url = state.get("input_url")
    if not input_url:
        return append_staged_sources([])

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
    founder = state.get("current_founder", "")
    if not founder:
        return append_staged_sources([])

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

