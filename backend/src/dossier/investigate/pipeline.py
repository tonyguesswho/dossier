"""Linear single-pass RAG pipeline — the Phase 2 orchestrator.

One invocation = one complete investigation. Sequential stages per CONTEXT.md D-04:
    gather (tools) → ingest (chunk+embed+insert) → retrieve (top-k per section)
  → synthesize (one LLM call → Brief) → ground (substring-match → claims) → complete.

This function is called from FastAPI BackgroundTasks in Plan 02-09 (CONTEXT.md D-14).
Phase 3 swaps BackgroundTasks for `boto3.lambda.invoke(InvocationType='Event', ...)` and
wraps this function as a Lambda handler (`dossier.investigate.pipeline:handler`) —
that's a ≤2-line change confined to a new `handler(event, context)` shim at the bottom
of this file. The core `run_investigation` function is unchanged.

Langfuse observability:
  - One root span per investigation (PATTERNS.md §pipeline.py).
  - One child span per stage (gather, ingest, retrieve, synthesize, ground).
  - trace_id recorded to investigations.langfuse_trace_id for Phase 4 scorecard deep-link.
  - lambda_sleep=False (local dev); Phase 3 flips to True on the Lambda handler shim.

Fail-fast behavior (CONTEXT.md D-05):
  - Any PipelineError / exception → status='failed', error=str(exc).
  - Partial progress persisted up to the failure point (sources/chunks stay; Phase 4
    eval-replay can still score a failed investigation against its partial corpus).

Firecrawl budget hygiene (T-02-08-04):
  - The wrapper's module-level _BUDGET dict counts crawls by investigation_id.
  - Pipeline pops the id in the finally block so a crashing pipeline does not
    leave a stale entry that would block retries.

Rejected alternatives:
  - asyncio.gather for parallel tool calls: deferred to Phase 3 LangGraph fan-out (D-04).
  - arq/Celery worker: requires Redis + extra docker-compose service (D-14).
  - Encode context hint as a separate DB column: would require migration 0003. Phase 3
    rewrites this module via LangGraph state anyway — not worth the migration churn.
    Phase 2 hack: encode hint in input_ref via HINT_SEPARATOR.
  - Swallow exceptions silently: D-05 forbids. Status=failed + error=str(exc) always.

Phase coverage:
  - Phase 2: linear, no reflection, three sources (Exa+GitHub+Firecrawl).
  - Phase 3: replaced by LangGraph graph with planner + verifier + 5 sources.
"""
from __future__ import annotations

import logging
import re
import traceback
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.core.exceptions import PipelineError
from dossier.investigate.ground import ground_claims
from dossier.investigate.ingest import ingest_tool_results
from dossier.investigate.retrieve import RetrievedChunk, retrieve_top_k
from dossier.investigate.synthesize import synthesize_brief
from dossier.investigate.tools import exa as exa_tool
from dossier.investigate.tools import firecrawl as firecrawl_tool
from dossier.investigate.tools import github as github_tool
from dossier.investigate.tools.types import ToolResult
from dossier.models import Brief, BriefClaim
from dossier.observability import flush_and_shutdown, get_langfuse_client

logger = logging.getLogger(__name__)

# Phase-2-only encoding: `{value}\n---HINT---\n{hint}` inside `investigations.input_ref`
# when the user provided a context hint. Ugly but Phase 3's LangGraph state replaces it.
# Migration 0003 was rejected because Phase 3 rewrites this module entirely.
HINT_SEPARATOR: str = "\n---HINT---\n"

# Six section-biased retrieval queries — one per Brief section. Query reformulation
# (not metadata filter) per retrieve.py / CONTEXT §Claude's Discretion.
_SECTION_QUERY_SUFFIXES: list[str] = [
    "founders background",
    "company overview funding",
    "market size competitors",
    "product features",
    "risks concerns",
    "questions to ask investors",
]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _read_investigation(engine: Engine, investigation_id: UUID) -> tuple[str, str]:
    """Return (input_type, input_ref) for the row. Raises PipelineError if not found."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT input_type, input_ref FROM investigations WHERE id = :id"),
            {"id": str(investigation_id)},
        ).fetchone()
    if row is None:
        raise PipelineError(f"Investigation not found: {investigation_id}")
    return row.input_type, row.input_ref


def _update_status(
    engine: Engine,
    investigation_id: UUID,
    status: str,
    *,
    error: Optional[str] = None,
    brief_markdown: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Single UPDATE query covering all the fields pipeline mutates.

    Uses COALESCE so callers can pass None for fields they don't want to touch
    on a given transition (e.g., status='gathering' with trace_id only).
    completed_at is set on terminal states ('complete' or 'failed') via CASE.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE investigations
                SET status = CAST(:s AS investigation_status),
                    error = COALESCE(:err, error),
                    brief_markdown = COALESCE(:md, brief_markdown),
                    langfuse_trace_id = COALESCE(:tid, langfuse_trace_id),
                    completed_at = CASE
                        WHEN :s IN ('complete','failed') THEN now()
                        ELSE completed_at
                    END
                WHERE id = :id
                """
            ),
            {
                "id": str(investigation_id),
                "s": status,
                "err": error,
                "md": brief_markdown,
                "tid": trace_id,
            },
        )


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------


def _resolve_inputs(
    input_type: str, input_ref: str
) -> tuple[str, str | None, str | None]:
    """Return (company_or_value, seed_url, context_hint) from the row's input_ref.

    The Phase 2 hack: input_ref is `{value}{HINT_SEPARATOR}{hint}` when a hint
    exists; otherwise just `{value}`. Plan 02-09's route handler writes this
    format; this function reads it.
    """
    value, _, hint = input_ref.partition(HINT_SEPARATOR)
    hint_clean: str | None = hint.strip() if hint else None

    if input_type == "name":
        return value.strip(), None, hint_clean
    if input_type == "url":
        # For Phase 2, treat the URL as both company display name and seed URL.
        # Phase 3's planner node will do a proper name resolution.
        url = value.strip()
        return url, url, hint_clean
    if input_type == "deck":
        # Phase 5-lite (Plan 03-13): the deck's text is pre-ingested into
        # source_chunks BEFORE run_investigation is called, so _gather will
        # skip tool calls and the pipeline proceeds straight to retrieve+synth.
        # Use the filename (stem, dashes → spaces) as the "company" display
        # name — good enough for the synthesizer's system prompt and the
        # brief header.
        filename = value.strip() or "pitch deck"
        stem = filename.rsplit(".", 1)[0]
        # Strip timestamp-ish noise like -170823132244 and re-space
        display = re.sub(r"[-_]\d{6,}.*$", "", stem).replace("-", " ").replace("_", " ").strip()
        return display or stem or filename, None, hint_clean
    raise PipelineError(f"Unknown input_type: {input_type!r}")


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def _gather(
    company: str, seed_url: str | None, investigation_id: UUID
) -> list[ToolResult]:
    """Run the three tools sequentially (D-04).

    Fail-open per tool: Exa/GitHub/Firecrawl failures are logged and swallowed so
    one dead source does not kill the investigation (CONTEXT §Claude's Discretion
    — partial brief > failed investigation). A fully empty result list still
    proceeds to ingest → synthesize; the synthesizer emits a thin brief.
    """
    results: list[ToolResult] = []
    seen_urls: set[str] = set()

    # Exa: three diverse queries so the corpus isn't dominated by the
    # company's marketing pages. Broad name query surfaces the homepage /
    # feature pages; founder-angled query catches bios / press; funding
    # query catches TechCrunch / Crunchbase-style coverage. Empirically
    # the founder query is what most often pulls a CEO name into a brief
    # for seed-stage companies whose own site doesn't highlight the team.
    exa_queries = [
        company,
        f"{company} founders CEO team",
        f"{company} funding raised investors",
    ]
    for q in exa_queries:
        try:
            for r in exa_tool.search(q, num_results=5):
                if r.url in seen_urls:
                    continue
                seen_urls.add(r.url)
                results.append(r)
        except Exception:  # noqa: BLE001 — fail-open per CONTEXT.md §Claude's Discretion
            logger.warning("Exa stage failed for query=%r (fail-open)", q, exc_info=True)

    # GitHub: INVEST-02 — use company as the founder search term (simple Phase 2 heuristic).
    # Phase 3 will extract founder names from Exa results first, then call GitHub per-founder.
    try:
        results.extend(github_tool.fetch_founder_profile(company))
    except Exception:  # noqa: BLE001
        logger.warning("GitHub stage failed (fail-open)", exc_info=True)

    # Firecrawl: crawl the seed URL if present; else the first Exa 'web' URL.
    # Budget ≤1 call/investigation enforced inside the wrapper (D-13).
    crawl_target = seed_url
    if crawl_target is None:
        for r in results:
            if r.source_kind == "web":
                crawl_target = r.url
                break
    if crawl_target:
        try:
            results.extend(
                firecrawl_tool.crawl_seed_url(
                    crawl_target, investigation_id=str(investigation_id)
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("Firecrawl stage failed (fail-open)", exc_info=True)

    return results


def _retrieve_all_sections(
    investigation_id: UUID, company: str
) -> list[RetrievedChunk]:
    """Retrieve top-k per section, dedupe by chunk_id.

    Six queries — one per Brief section — concatenated and deduped. The resulting
    unified chunk list is what the synthesizer prompt sees. Earlier occurrences
    win on duplicate chunk_id (setdefault).
    """
    seen: dict[str, RetrievedChunk] = {}
    for suffix in _SECTION_QUERY_SUFFIXES:
        query = f"{company} {suffix}"
        for chunk in retrieve_top_k(investigation_id, query, k=6):
            seen.setdefault(str(chunk.chunk_id), chunk)
    return list(seen.values())


def _brief_to_markdown(
    brief: Brief, url_by_chunk: dict[str, str]
) -> str:
    """Deterministic markdown render for Phase 2 (plain bullets per section).

    Phase 4 (BRIEF-02) replaces with inline citation popovers. Phase 2 is plain
    bullets with a `([source](url))` suffix when the cited chunk's url is known.
    Unknown source_chunk_ids render as `(source)` without a link.

    url_by_chunk is a chunk_id → url map covering ALL source_chunks for the
    investigation (not just the top-k retrieved set). Built by the caller via
    a SQL JOIN so grounded claims whose chunk_id lives outside the retrieved
    set still get a clickable link (this was the pre-Phase-4 Facebook-brief
    bug where every claim rendered as literal `(source)` text).
    """

    # Field name (plural risk_flags, suggested_questions) → display heading.
    SECTION_HEADINGS: list[tuple[str, str]] = [
        ("Founders", "founders"),
        ("Company", "company"),
        ("Market", "market"),
        ("Product", "product"),
        ("Risk Flags", "risk_flags"),
        ("Suggested Questions", "suggested_questions"),
    ]

    out: list[str] = []
    for heading, field in SECTION_HEADINGS:
        out.append(f"## {heading}\n")
        claims: list[BriefClaim] = getattr(brief, field)
        if not claims:
            out.append("_No claims synthesized for this section._\n")
            continue
        for c in claims:
            url = url_by_chunk.get(c.source_chunk_id, "")
            suffix = f" ([source]({url}))" if url else ""
            out.append(f"- {c.claim_text}{suffix}")
        out.append("")  # blank line between sections
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


def run_investigation(
    investigation_id: UUID, *, engine: Optional[Engine] = None
) -> None:
    """Run the complete investigation pipeline for one row. Updates DB + Langfuse.

    Contract:
      - Reads (input_type, input_ref) from the investigations row.
      - Writes status through the full enum: gathering → synthesizing → grounding → complete.
      - On failure at any stage: status='failed' + error=str(exc) (D-05 fail-fast).
      - Never raises — Plan 02-09 dispatches via BackgroundTasks which swallow exceptions
        anyway, but we belt-and-suspend here so callers can treat this as a true
        fire-and-forget target.
      - Always flushes Langfuse in the finally block.
      - Always pops the Firecrawl budget entry in the finally block (T-02-08-04).

    Args:
        investigation_id: UUID of the investigations row to run. MUST already exist
            (Plan 02-09's POST /investigations INSERTs it in status='queued' before
            dispatching BackgroundTasks).
        engine: optional SQLAlchemy Engine (tests pass a fake; prod uses get_engine()).
    """
    eng = engine if engine is not None else get_engine()

    # Langfuse client is non-strict — a missing key downgrades to a no-op wrapper so
    # a Langfuse outage never crashes an investigation (graceful-degrade per
    # observability.py rule 4).
    lf_client = get_langfuse_client(strict=False)
    trace_id: str | None = None

    # Resolve inputs first; failures at this stage jump straight to status=failed
    # without opening a Langfuse span (nothing meaningful to trace — it would just
    # be an empty root).
    try:
        input_type, input_ref = _read_investigation(eng, investigation_id)
        company, seed_url, context_hint = _resolve_inputs(input_type, input_ref)
    except PipelineError as exc:
        _update_status(eng, investigation_id, "failed", error=str(exc))
        try:
            flush_and_shutdown(lf_client, lambda_sleep=False)
        except Exception:  # noqa: BLE001
            logger.warning("Langfuse flush_and_shutdown failed", exc_info=True)
        firecrawl_tool._BUDGET.pop(str(investigation_id), None)
        return
    except Exception as exc:  # noqa: BLE001
        _update_status(
            eng,
            investigation_id,
            "failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        try:
            flush_and_shutdown(lf_client, lambda_sleep=False)
        except Exception:  # noqa: BLE001
            logger.warning("Langfuse flush_and_shutdown failed", exc_info=True)
        firecrawl_tool._BUDGET.pop(str(investigation_id), None)
        return

    # Open the Langfuse root span + execute stages.
    try:
        # Deferred import — matches langfuse_smoke.py pattern and avoids any
        # import-order concern before get_langfuse_client() runs.
        from langfuse import propagate_attributes  # noqa: PLC0415

        with propagate_attributes(
            tags=["phase-2", "investigate"],
            trace_name=f"investigate-{company}",
        ):
            with lf_client.start_as_current_observation(
                as_type="span",
                name="investigate",
                input={
                    "investigation_id": str(investigation_id),
                    "input_type": input_type,
                },
                metadata={"phase": "02", "company": company},
            ) as root_span:
                try:
                    trace_id = lf_client.get_trace_id()
                except Exception:  # noqa: BLE001
                    trace_id = None

                try:
                    # Stage 1 — gather (status=gathering, trace_id recorded here)
                    _update_status(
                        eng, investigation_id, "gathering", trace_id=trace_id
                    )
                    with lf_client.start_as_current_observation(
                        as_type="span", name="gather"
                    ):
                        # Deck path (Plan 03-13): run_deck_investigation has
                        # already pre-ingested the extracted markdown as the
                        # sole ToolResult. Skip web gather + re-ingest.
                        if input_type == "deck":
                            logger.info(
                                "pipeline: skipping gather+ingest (deck path, chunks pre-ingested)"
                            )
                            tool_results: list[ToolResult] = []
                        else:
                            tool_results = _gather(company, seed_url, investigation_id)

                    # Stage 2 — ingest (still under 'gathering'; ingest is cheap)
                    with lf_client.start_as_current_observation(
                        as_type="span", name="ingest"
                    ):
                        if tool_results:
                            ingest_tool_results(
                                investigation_id, tool_results, engine=eng
                            )

                    # Stage 3 — retrieve + Stage 4 — synthesize, both under 'synthesizing'
                    _update_status(eng, investigation_id, "synthesizing")
                    with lf_client.start_as_current_observation(
                        as_type="span", name="retrieve"
                    ):
                        retrieved = _retrieve_all_sections(investigation_id, company)

                    with lf_client.start_as_current_observation(
                        as_type="span", name="synthesize"
                    ):
                        brief = synthesize_brief(
                            retrieved, company, context_hint=context_hint
                        )

                    # Stage 5 — ground
                    _update_status(eng, investigation_id, "grounding")
                    with lf_client.start_as_current_observation(
                        as_type="span", name="ground"
                    ):
                        ground_claims(investigation_id, brief, retrieved, engine=eng)

                    # Render + persist final brief
                    # Build full chunk_id → url map from DB so grounded claims
                    # whose chunk_id lives outside `retrieved` (top-k subset)
                    # still render a clickable ([source](url)) link.
                    with eng.connect() as conn:
                        url_rows = conn.execute(
                            text(
                                "SELECT sc.id::text AS chunk_id, s.url "
                                "FROM source_chunks sc "
                                "JOIN sources s ON sc.source_id = s.id "
                                "WHERE s.investigation_id = :inv_id"
                            ),
                            {"inv_id": investigation_id},
                        ).all()
                    url_by_chunk = {r.chunk_id: r.url for r in url_rows}
                    brief_md = _brief_to_markdown(brief, url_by_chunk)
                    _update_status(
                        eng,
                        investigation_id,
                        "complete",
                        brief_markdown=brief_md,
                    )
                    root_span.update(output={"status": "complete"})
                except PipelineError as exc:
                    logger.warning(
                        "Pipeline failed (PipelineError): %s", exc, exc_info=True
                    )
                    _update_status(
                        eng, investigation_id, "failed", error=str(exc)
                    )
                    root_span.update(output={"status": "failed", "error": str(exc)})
                except Exception as exc:  # noqa: BLE001
                    tb = traceback.format_exc()
                    logger.error("Pipeline failed (unhandled): %s\n%s", exc, tb)
                    _update_status(
                        eng,
                        investigation_id,
                        "failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    root_span.update(
                        output={"status": "failed", "error": str(exc)}
                    )
    finally:
        # T-02-08-04: reset Firecrawl per-investigation budget so a crashing
        # pipeline does not leave a stale count blocking retries.
        firecrawl_tool._BUDGET.pop(str(investigation_id), None)
        # Flush Langfuse traces. lambda_sleep=False for local dev;
        # Phase 3 Lambda shim flips this to True.
        try:
            flush_and_shutdown(lf_client, lambda_sleep=False)
        except Exception:  # noqa: BLE001
            logger.warning("Langfuse flush_and_shutdown failed", exc_info=True)


__all__ = ["HINT_SEPARATOR", "run_investigation"]
