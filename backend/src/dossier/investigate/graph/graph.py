"""build_graph() factory — compiles the DossierState investigation graph.

Call signature: build_graph(checkpointer) where checkpointer is an
AsyncPostgresSaver (Plan 03-03). Returns CompiledStateGraph.

Graph topology (D-04, 03-RESEARCH.md Pattern 2):
  START → planner → gather_fanout
  gather_fanout → [stage1_router] → exa_search / newsapi_search / firecrawl_crawl (parallel)
  All Stage-1 nodes → founder_extraction
  founder_extraction → [stage2_router] → github_founder×N / crunchbase_search (parallel)
  All Stage-2 nodes → ingest_and_embed
  ingest_and_embed → verifier
  verifier → [route] → gather_fanout (if should_regather) OR synthesizer
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
    """Compile and return the investigation StateGraph.

    Args:
        checkpointer: AsyncPostgresSaver instance (Plan 03-03).
                      Pass None only for local unit tests (uses no checkpointer).

    Returns:
        CompiledStateGraph ready for .ainvoke(state, config={"configurable": {"thread_id": investigation_id}})
    """
    builder = StateGraph(DossierState)

    # --- Nodes ---
    builder.add_node("planner", planner.run)
    builder.add_node("gather_fanout", gather_fanout.run)
    builder.add_node("exa_search", gather_fanout.run_exa)
    builder.add_node("newsapi_search", gather_fanout.run_newsapi)
    builder.add_node("firecrawl_crawl", gather_fanout.run_firecrawl)
    builder.add_node("founder_extraction", founder_extraction.run)  # Haiku 4.5 + FounderCandidates (Plan 03-05)
    builder.add_node("github_founder", gather_fanout.run_github_founder)
    builder.add_node("crunchbase_search", gather_fanout.run_crunchbase)
    builder.add_node("ingest_and_embed", ingest_and_embed.run)
    builder.add_node("verifier", verifier.run)
    builder.add_node("synthesizer", synthesizer.run)
    builder.add_node("finalize", finalize.run)

    # --- Edges ---
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "gather_fanout")

    # Stage 1 fan-out: gather_fanout → [exa_search, newsapi_search, firecrawl_crawl]
    builder.add_conditional_edges("gather_fanout", gather_fanout.stage1_router)

    # Stage 1 convergence: all Stage-1 nodes → founder_extraction
    # (BLOCKER-2 fix: founder_extraction must be in Wave 1 topology)
    builder.add_edge("exa_search", "founder_extraction")
    builder.add_edge("newsapi_search", "founder_extraction")
    builder.add_edge("firecrawl_crawl", "founder_extraction")

    # Stage 2 fan-out: founder_extraction → [stage2_router] → github_founder×N + crunchbase_search
    builder.add_conditional_edges("founder_extraction", gather_fanout.stage2_router)

    # Stage 2 convergence: all Stage-2 nodes → ingest_and_embed
    builder.add_edge("github_founder", "ingest_and_embed")
    builder.add_edge("crunchbase_search", "ingest_and_embed")

    builder.add_edge("ingest_and_embed", "verifier")

    # Verifier: conditional route back to gather_fanout OR forward to synthesizer
    builder.add_conditional_edges("verifier", verifier.route)

    builder.add_edge("synthesizer", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_graph"]
