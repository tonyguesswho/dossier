"""Investigation graph factory.

Topology:
    START → planner → gather_fanout
        Stage 1 fan-out: exa_search ‖ newsapi_search ‖ firecrawl_crawl
        ↓ converge
    founder_extraction
        Stage 2 fan-out: github_founder×N ‖ crunchbase_search
        ↓ converge
    ingest_and_embed → verifier
    verifier → gather_fanout (regather) | synthesizer
    synthesizer → finalize → END
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes import (
    finalize,
    founder_extraction,
    gather_fanout,
    ingest_and_embed,
    planner,
    synthesizer,
    verifier,
)
from .state import DossierState


def build_graph(checkpointer=None):
    """Compile the investigation StateGraph. Pass None for checkpointer in tests."""
    builder = StateGraph(DossierState)

    builder.add_node("planner", planner.run)
    builder.add_node("gather_fanout", gather_fanout.run)
    builder.add_node("exa_search", gather_fanout.run_exa)
    builder.add_node("newsapi_search", gather_fanout.run_newsapi)
    builder.add_node("firecrawl_crawl", gather_fanout.run_firecrawl)
    builder.add_node("founder_extraction", founder_extraction.run)
    builder.add_node("github_founder", gather_fanout.run_github_founder)
    builder.add_node("crunchbase_search", gather_fanout.run_crunchbase)
    builder.add_node("ingest_and_embed", ingest_and_embed.run)
    builder.add_node("verifier", verifier.run)
    builder.add_node("synthesizer", synthesizer.run)
    builder.add_node("finalize", finalize.run)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "gather_fanout")

    builder.add_conditional_edges("gather_fanout", gather_fanout.stage1_router)
    builder.add_edge("exa_search", "founder_extraction")
    builder.add_edge("newsapi_search", "founder_extraction")
    builder.add_edge("firecrawl_crawl", "founder_extraction")

    builder.add_conditional_edges("founder_extraction", gather_fanout.stage2_router)
    builder.add_edge("github_founder", "ingest_and_embed")
    builder.add_edge("crunchbase_search", "ingest_and_embed")

    builder.add_edge("ingest_and_embed", "verifier")
    builder.add_conditional_edges("verifier", verifier.route)

    builder.add_edge("synthesizer", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_graph"]
