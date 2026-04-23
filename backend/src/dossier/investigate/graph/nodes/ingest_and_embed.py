"""Ingest & embed node — chunk + classify + embed + persist.

Pipeline order (BLOCKER-4 / WARNING-4 / D-06 / D-07):
  1. Collect all ToolResult objects from the module-level cache (no DB, no LLM).
     The gather_fanout tool nodes call cache_tool_result() for every result they
     produce, so by the time the Stage-2 branches converge here every raw
     source text is in memory keyed by (investigation_id, url).
  2. Chunk ALL results in memory via ingest._chunk_text (no DB, no LLM).
  3. Classify ALL chunks in parallel via asyncio.gather(*_classify_chunk(c) for c
     in all_chunks) — DB session is NOT open during these LLM calls (WARNING-4:
     RDS Proxy pool pressure avoidance; Pitfall 7.4).
  4. Partition chunks: clean vs injection.
  5. Open ONE short-lived DB session for bulk writes:
       a. Call the Phase 2 sync ingest_tool_results() via asyncio.to_thread for
          the clean path — it re-chunks + embeds + inserts source rows, keeping
          the Phase 2 / Phase 3 paths bit-identical.
       b. INSERT injection chunks into injection_attempts (D-07).
  6. Clear the cache for this investigation.

Injection classifier stub:
  - _classify_chunk() returns ("clean", "") for EVERY chunk in this plan.
    Plan 03-08 replaces the body with the real Haiku 4.5 judge from D-06.
  - The chunk-first / classify-before-DB ordering is established NOW so Plan
    03-08 only swaps the classifier body; the BLOCKER-4 invariant (classify
    runs on post-chunking text, so injections past the 2000-char mark cannot
    sneak through) is structural, not tied to the classifier implementation.

Why a module-level cache instead of carrying ToolResult text through state:
  ARCHITECTURE.md §9 anti-pattern forbids raw source text in LangGraph state.
  The cache sits outside state — populated by gather_fanout tool nodes, drained
  here, cleared on exit. investigate-lambda is single-invocation per container,
  so there is no cross-investigation contamination risk (T-03-06-02).

Why asyncio.to_thread for ingest_tool_results:
  Phase 2 ingest.py is sync (engine.begin(), sync HTTP embedding call). Wrapping
  it in to_thread preserves the audited logic verbatim — zero behavioural drift
  vs. Phase 2's still-running pipeline.py path.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from uuid import UUID

from sqlalchemy import text

from dossier.investigate.tools.types import ToolResult

from ..state import DossierState

logger = logging.getLogger(__name__)


# Module-level cache: {investigation_id: {url: ToolResult}}.
# Populated by gather_fanout tool nodes (run_exa / run_newsapi / run_firecrawl /
# run_github_founder / run_crunchbase) via cache_tool_result(); drained here.
_TOOL_RESULT_CACHE: dict[str, dict[str, ToolResult]] = defaultdict(dict)


def cache_tool_result(investigation_id: str, result: ToolResult) -> None:
    """Register a ToolResult for later ingest. Called by gather_fanout tool nodes.

    Keyed by (investigation_id, url) so re-gather passes (D-02 reflection loop)
    overwrite rather than duplicate. If two different tools return the same URL
    (Exa + Firecrawl hitting the same landing page), the later write wins —
    which is fine because ingest's content_hash dedup will skip the row anyway.
    """
    _TOOL_RESULT_CACHE[investigation_id][result.url] = result


def _drain_cache(investigation_id: str) -> list[ToolResult]:
    """Pop and return all cached ToolResults for this investigation."""
    bucket = _TOOL_RESULT_CACHE.pop(investigation_id, None)
    if not bucket:
        return []
    return list(bucket.values())


async def _classify_chunk(chunk_text: str) -> tuple[str, str]:
    """Injection classifier STUB — Plan 03-08 replaces this with the Haiku 4.5 judge (D-06).

    Returns (verdict, reason). Stub always returns ("clean", "") so the graph
    runs end-to-end until Plan 03-08 wires the real classifier. The async
    signature is preserved so Plan 03-08 is a body swap, not a signature change.

    Called OUTSIDE any DB session block (WARNING-4): RDS Proxy pins connections
    that hold session state during long LLM calls, so we classify-then-write
    rather than write-while-classifying.
    """
    _ = chunk_text  # unused in stub; Plan 03-08 wires the real prompt input
    return "clean", ""


async def run(state: DossierState) -> dict:
    """Chunk all results → classify all chunks (no DB open) → bulk DB writes.

    Returns empty dict — retrieved_chunks in state is untouched because
    downstream nodes (synthesizer, finalize) query pgvector by investigation_id
    rather than dereference chunk_ids from state. Filling chunk_id placeholders
    would require a SELECT after INSERT with no consumer; wasteful.
    """
    # Deferred import: keep this module importable in environments that lack
    # the DB or LangChain deps (e.g. lightweight unit tests that only touch
    # the cache or classifier stub). Matches founder_extraction's pattern.
    from dossier.core.db import get_async_session
    from dossier.investigate.ingest import _chunk_text, ingest_tool_results

    investigation_id_str = state["investigation_id"]
    investigation_uuid = UUID(investigation_id_str)

    # ---- Step 1: drain cache (no DB, no LLM) -------------------------------
    results_to_ingest = _drain_cache(investigation_id_str)
    if not results_to_ingest:
        logger.warning(
            "ingest_and_embed: no cached tool results for investigation_id=%s "
            "(gather_fanout may have emitted zero results across all branches)",
            investigation_id_str,
        )
        return {}

    logger.info(
        "ingest_and_embed: draining %d cached tool result(s) for investigation_id=%s",
        len(results_to_ingest),
        investigation_id_str,
    )

    # ---- Step 2: chunk ALL results in memory (no DB, no LLM) ---------------
    # Plan 03-08 consumes this per-chunk view to run the real classifier. In
    # this plan the partitioning is trivial (all-clean) but the structure is
    # already in place so 03-08 is a classifier-body swap.
    chunks_by_result: list[tuple[ToolResult, list[str]]] = []
    total_chunks = 0
    for result in results_to_ingest:
        try:
            spans = _chunk_text(result.text)
        except Exception:  # noqa: BLE001 — per-source fail-open (one bad doc ≠ failed investigation)
            logger.warning(
                "ingest_and_embed: _chunk_text failed for url=%s — skipping source",
                result.url,
                exc_info=True,
            )
            spans = []
        chunk_texts = [s.text for s in spans]
        chunks_by_result.append((result, chunk_texts))
        total_chunks += len(chunk_texts)

    if total_chunks == 0:
        logger.warning(
            "ingest_and_embed: 0 chunks produced from %d tool result(s) for investigation_id=%s",
            len(results_to_ingest),
            investigation_id_str,
        )
        return {}

    # ---- Step 3: classify ALL chunks in parallel — NO DB session open ------
    # WARNING-4: RDS Proxy pins a connection whenever the session holds state
    # during an outbound network call. Running asyncio.gather before the
    # `async with get_async_session()` block guarantees zero pool pressure.
    flat_chunks: list[tuple[ToolResult, str]] = [
        (result, chunk_text)
        for result, chunk_texts in chunks_by_result
        for chunk_text in chunk_texts
    ]
    verdicts = await asyncio.gather(
        *(_classify_chunk(chunk_text) for _, chunk_text in flat_chunks)
    )

    # ---- Step 4: partition clean vs injection (still no DB) ----------------
    clean_urls: set[str] = set()
    injection_chunks: list[tuple[ToolResult, str, str]] = []  # (result, text, reason)
    for (result, chunk_text), (verdict, reason) in zip(flat_chunks, verdicts, strict=True):
        if verdict == "injection":
            logger.warning(
                "ingest_and_embed: quarantining injection chunk from url=%s reason=%r",
                result.url,
                reason,
            )
            injection_chunks.append((result, chunk_text, reason))
        else:
            clean_urls.add(result.url)

    # Per-source clean-list: a ToolResult is clean iff none of its chunks were
    # flagged. In stub mode clean_urls == {every url}; the real classifier in
    # Plan 03-08 will tighten this (a single injection chunk quarantines the
    # whole source, matching the D-07 "never reach synthesizer" guarantee).
    clean_tool_results: list[ToolResult] = [
        result
        for result in results_to_ingest
        if result.url in clean_urls
        and not any(r.url == result.url for r, _, _ in injection_chunks)
    ]

    logger.info(
        "ingest_and_embed: %d/%d chunks clean; %d injection chunks across %d source(s); "
        "ingesting %d clean source(s) for investigation_id=%s",
        total_chunks - len(injection_chunks),
        total_chunks,
        len(injection_chunks),
        len({r.url for r, _, _ in injection_chunks}),
        len(clean_tool_results),
        investigation_id_str,
    )

    # ---- Step 5: bulk writes under a single short-lived async session ------
    #
    # 5a) clean path: hand off to Phase 2's sync ingest_tool_results() via
    #     asyncio.to_thread. It opens its OWN sync Engine connection — that is
    #     fine: the sync Engine talks through the same RDS Proxy endpoint, and
    #     `prepare_threshold=0` is wired for both engines (see core/db.py).
    #     Bridging sync → async via to_thread keeps Phase 2's audited chunk-
    #     embed-insert logic bit-identical so the Phase 4 eval harness replay
    #     does not see behavioural drift.
    if clean_tool_results:
        await asyncio.to_thread(
            ingest_tool_results, investigation_uuid, clean_tool_results
        )

    # 5b) injection path: INSERT quarantined chunks into injection_attempts.
    #     D-07 requires raw payloads persisted for red-team analysis; table was
    #     created by migration 0004. Use the async session because this is a
    #     fresh write with no sync-path equivalent in Phase 2.
    if injection_chunks:
        async with get_async_session() as session:
            async with session.begin():
                for result, chunk_text, reason in injection_chunks:
                    await session.execute(
                        text(
                            """
                            INSERT INTO injection_attempts
                                (investigation_id, url, raw_payload, verdict, reason)
                            VALUES
                                (CAST(:inv AS UUID), :url, :payload, :verdict, :reason)
                            """
                        ),
                        {
                            "inv": investigation_id_str,
                            "url": result.url,
                            "payload": chunk_text,
                            "verdict": "injection",
                            "reason": reason,
                        },
                    )

    return {}


__all__ = ["cache_tool_result", "run"]
