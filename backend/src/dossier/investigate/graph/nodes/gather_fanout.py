"""Two-stage fan-out orchestration via langgraph.types.Send (D-04, 03-CONTEXT.md).

Stage 1: Exa + NewsAPI + Firecrawl (if input_url present) — parallel Send.
  After Stage 1 completes: founder_extraction (stub here; Plan 03-05 fills Haiku 4.5 call).
Stage 2 (after founder_extraction): GitHub-per-founder + Crunchbase — parallel Send.

Actual tool calls implemented in Plan 03-04 (newsapi/crunchbase wrappers).
The existing exa.py/github.py/firecrawl.py wrappers are imported directly.
"""
from __future__ import annotations

import logging

from langgraph.types import Send

from ..state import DossierState

logger = logging.getLogger(__name__)


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


# --- Tool nodes (stubs; filled by Plan 03-04 for newsapi/crunchbase, reuse existing for exa/github/firecrawl) ---


async def run_exa(state: DossierState) -> dict:
    """Exa Stage-1 search. Full implementation in Plan 03-04."""
    raise NotImplementedError("run_exa: implemented in Plan 03-04")


async def run_newsapi(state: DossierState) -> dict:
    """NewsAPI search. Full implementation in Plan 03-04."""
    raise NotImplementedError("run_newsapi: implemented in Plan 03-04")


async def run_firecrawl(state: DossierState) -> dict:
    """Firecrawl crawl (when input_url present). Full implementation in Plan 03-04."""
    raise NotImplementedError("run_firecrawl: implemented in Plan 03-04")


async def run_github_founder(state: DossierState) -> dict:
    """Per-founder GitHub search. Full implementation in Plan 03-04."""
    raise NotImplementedError("run_github_founder: implemented in Plan 03-04")


async def run_crunchbase(state: DossierState) -> dict:
    """Crunchbase enrichment. Full implementation in Plan 03-04."""
    raise NotImplementedError("run_crunchbase: implemented in Plan 03-04")
