"""investigate-lambda handler: runs the LangGraph investigation graph.

This module is the entry point for the investigate-lambda container image.
It is also importable locally for `DOSSIER_DISPATCH_MODE=local` dev runs
(see api/routes/investigations.py _dispatch_pipeline).

Security (T-03-03-01): If DOSSIER_AUTH_DEV_BYPASS is set AND
AWS_LAMBDA_FUNCTION_NAME is set, this handler refuses to start — the bypass
var must never leak to a deployed Lambda (03-CONTEXT.md security threat,
dev-mode bypass leak). Enforced by _check_security_invariants() at handler
entry so a misconfigured deploy fails at invocation, not silently.

Lambda flush pattern (PLAT-04 / CLAUDE.md locked decision):
    flush() + shutdown() + sleep(15) BEFORE handler returns.
    Without sleep(15), ~30% of Langfuse traces are lost because the Lambda
    execution context is frozen before the Langfuse SDK's background
    HTTP sender drains. The sleep is implemented inside
    observability.flush_and_shutdown(lambda_sleep=True).

AsyncPostgresSaver pool config (03-RESEARCH.md Pattern 4, Pitfall 7.4):
    prepare_threshold=0 CRITICAL: disables prepared statements on psycopg3
    connections. Without this, RDS Proxy detects session-state changes
    (DEALLOCATE ALL + prepared statement registration) and pins the
    connection to this client, exhausting the t3.micro's ~85-connection
    cap under Lambda concurrency.

    autocommit=True is required by AsyncPostgresSaver.setup() for the
    checkpoint table DDL (Pitfall 7.2).

Pool lifecycle (03-RESEARCH.md Pattern 4):
    Pool is created at module level so warm Lambda invocations reuse it.
    open=False + await _pool.open() at first use avoids event-loop
    initialization issues at module import time (module init runs outside
    the asyncio loop on some Lambda runtimes).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Module-level pool — created once per Lambda container lifetime.
# Warm invocations share the same AsyncConnectionPool + AsyncPostgresSaver.
_pool: Any = None
_checkpointer: Any = None


def _check_security_invariants() -> None:
    """Refuse to start if dev-bypass is set in a deployed Lambda context.

    T-03-03-01 mitigation: raising at handler entry makes the Lambda
    invocation fail loudly, guaranteeing the misconfiguration is caught
    on first call rather than silently bypassing auth.
    """
    from dossier.core.settings import get_settings  # noqa: PLC0415 — defer
    s = get_settings()
    if s.dossier_auth_dev_bypass and s.is_lambda_runtime:
        raise RuntimeError(
            "SECURITY: DOSSIER_AUTH_DEV_BYPASS must not be set when running as a "
            "deployed Lambda (AWS_LAMBDA_FUNCTION_NAME is present). "
            "Unset DOSSIER_AUTH_DEV_BYPASS before deploying."
        )


async def _get_checkpointer() -> Any:
    """Lazy-init AsyncPostgresSaver backed by a psycopg3 pool with prepare_threshold=0.

    Cached at module level so warm Lambda invocations reuse the same pool.
    setup() is idempotent — safe to call once per cold start.
    """
    global _pool, _checkpointer

    # Deferred imports keep this module importable in environments that
    # don't have psycopg_pool / langgraph-checkpoint-postgres installed
    # (e.g., the api-lambda image which only needs Mangum + FastAPI).
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    if _pool is None:
        from dossier.core.settings import get_settings  # noqa: PLC0415 — defer
        # database_url_libpq is the SQLAlchemy URL with the +psycopg driver
        # suffix stripped — psycopg_pool refuses the SQLAlchemy form.
        _pool = AsyncConnectionPool(
            conninfo=get_settings().database_url_libpq,
            min_size=0,  # open connections lazily — Lambda warms up fast, not worth pre-opening
            max_size=2,  # single-invocation Lambda; 2 connections is enough
            # psycopg_pool >=3.2 default min_size is 4; must explicitly pin ≤ max_size
            # or the pool raises "max_size must be greater or equal than min_size" on init.
            kwargs={
                "autocommit": True,       # required by AsyncPostgresSaver.setup()
                "prepare_threshold": 0,   # CRITICAL: prevents RDS Proxy pinning (Pitfall 7.4)
            },
            open=False,  # defer open() to runtime asyncio loop
        )
        await _pool.open()
        _checkpointer = AsyncPostgresSaver(_pool)
        await _checkpointer.setup()  # idempotent: creates LangGraph tables if not exist
        logger.info("runner: AsyncPostgresSaver pool initialized")

    return _checkpointer


async def run_graph(investigation_id: str) -> None:
    """Run the investigation graph for investigation_id.

    Thread ID = investigation_id (one LangGraph thread per investigation).
    Resumes from last checkpoint if a prior run was interrupted (ROADMAP SC#3).

    Flush contract: even if graph.ainvoke() raises, the finally block flushes
    Langfuse traces with the 15s Lambda sleep — trace loss on failure paths
    is how bugs get missed in prod.
    """
    # Deferred imports so test environments can monkeypatch DATABASE_URL /
    # Langfuse keys before these modules read os.environ at import time.
    from sqlalchemy import text

    from dossier.core.db import get_async_session
    from dossier.investigate.graph import build_graph
    from dossier.observability import (
        flush_and_shutdown,
        get_langchain_callback_handler,
        get_langfuse_client,
    )

    # strict=False: missing Langfuse creds degrade to no-op, do not crash the run.
    langfuse_client = get_langfuse_client(strict=False)

    # Read investigation row. The investigations table has no `company_name`
    # or `context_hint` columns — those are encoded inside `input_ref` via
    # HINT_SEPARATOR (Phase 2 convention from pipeline.py / Plan 02-09).
    # We parse them out here using the same separator.
    from dossier.investigate.render import HINT_SEPARATOR  # noqa: PLC0415
    async with get_async_session() as session:
        result = await session.execute(
            text(
                "SELECT input_type, input_ref, langfuse_trace_id "
                "FROM investigations WHERE id = :id"
            ),
            {"id": investigation_id},
        )
        row = result.fetchone()

    if not row:
        logger.error("runner: investigation_id=%s not found", investigation_id)
        if langfuse_client:
            flush_and_shutdown(langfuse_client, lambda_sleep=True)
        return

    input_type, input_ref, langfuse_trace_id = row
    value, _, hint = (input_ref or "").partition(HINT_SEPARATOR)
    company_name = value.strip() or "unknown"
    context_hint = hint.strip() if hint else None
    input_url = value.strip() if input_type == "url" else None

    checkpointer = await _get_checkpointer()
    graph = build_graph(checkpointer=checkpointer)

    # Wire Langfuse per-node spans (D-03, PLAT-04, ROADMAP SC#4).
    # Attaches to the existing investigations.langfuse_trace_id so these
    # node spans continue the trace that api-lambda started when the
    # investigation row was created (Phase 2 plumbing, unchanged here).
    #
    # strict=False: a Langfuse outage (creds missing / SDK init failure /
    # network down at startup) returns None; config["callbacks"] then
    # becomes [] and the graph runs untraced rather than aborting.
    # T-03-07-02 mitigation: tracing is never a hard dependency.
    lf_handler = get_langchain_callback_handler(
        trace_id=langfuse_trace_id,
        session_id=str(investigation_id),
        strict=False,
    )

    config: dict[str, Any] = {
        "configurable": {
            "thread_id": str(investigation_id),
        },
        # Empty list when lf_handler is None — LangGraph treats [] as "no
        # callbacks" and proceeds normally (same code path as a successful
        # handler that just happens to be no-op).
        "callbacks": [lf_handler] if lf_handler is not None else [],
        # langfuse_session_id is read by the Langfuse 4.x LangChain
        # integration from run metadata (not from the CallbackHandler
        # constructor, which in 4.x only accepts public_key + trace_context).
        # This groups all per-investigation spans under one Langfuse
        # Session in the UI, independent of trace_id continuity.
        "metadata": {"langfuse_session_id": str(investigation_id)},
    }

    # Resume detection (ROADMAP SC#3 + test_checkpoint_resume.py):
    # LangGraph's ainvoke() semantics — passing a dict re-runs from START with
    # that input; passing None continues from the last checkpoint. On Lambda
    # kill-and-replay we MUST pass None for the checkpoint resume to skip the
    # already-completed nodes. Detecting an existing interrupted thread is
    # done via aget_state: an unseen thread has next=() and created_at=None;
    # an interrupted thread has a non-empty `next` tuple (the node that
    # would have run next when the crash happened).
    existing_state = await graph.aget_state(config)
    is_resume = bool(existing_state.next) and existing_state.created_at is not None

    if is_resume:
        logger.info(
            "runner: resuming investigation_id=%s from checkpoint (next=%s)",
            investigation_id,
            existing_state.next,
        )
        graph_input: Any = None
    else:
        graph_input = {
            "investigation_id": str(investigation_id),
            "company": company_name,
            "context_hint": context_hint,
            "input_url": input_url,
            "input_type": input_type,
            "reflection_count": 0,
            "should_regather": False,
            "targeted_sections": [],
            "founder_candidates": [],
            "retrieved_chunks": [],
            "draft_claims": [],
            "grounded_claims": [],
        }

    try:
        logger.info("runner: starting graph for investigation_id=%s", investigation_id)
        await graph.ainvoke(graph_input, config=config)
        logger.info("runner: graph completed for investigation_id=%s", investigation_id)
    except Exception:
        logger.exception("runner: graph failed for investigation_id=%s", investigation_id)
        # Mark investigation as failed so the frontend poll surfaces the error.
        # A separate session is intentional — the earlier session may be in
        # an aborted transaction state after a mid-graph DB error.
        try:
            async with get_async_session() as session:
                async with session.begin():
                    await session.execute(
                        text("UPDATE investigations SET status='failed' WHERE id = :id"),
                        {"id": investigation_id},
                    )
        except Exception:
            logger.exception(
                "runner: failed to mark investigation_id=%s as failed", investigation_id
            )
        raise
    finally:
        # PLAT-04 / CLAUDE.md: flush Langfuse before Lambda returns.
        # lambda_sleep=True adds the 15s drain window (STACK.md §2.6).
        if langfuse_client:
            flush_and_shutdown(langfuse_client, lambda_sleep=True)


def handler(event: dict, context: Any) -> dict:
    """AWS Lambda handler entry point for investigate-lambda.

    Event shape: {"investigation_id": "<UUID>"}
    Called via boto3 InvocationType='Event' (fire-and-forget) from api-lambda.

    Returns a dict so CloudWatch shows the outcome; the caller (api-lambda's
    async invoke) does not inspect the return value.
    """
    _check_security_invariants()

    investigation_id = event.get("investigation_id")
    if not investigation_id:
        logger.error("runner.handler: missing investigation_id in event")
        return {"statusCode": 400, "body": "missing investigation_id"}

    asyncio.run(run_graph(str(investigation_id)))
    return {"statusCode": 200, "body": "ok"}


__all__ = ["handler", "run_graph"]
