"""investigate-lambda handler — runs the investigation graph.

Same module is importable for local dispatch (`DOSSIER_DISPATCH_MODE=local`).

Two non-obvious bits worth knowing:

1. Refuses to start if `DOSSIER_AUTH_DEV_BYPASS` is set inside a deployed
   Lambda — that env var disables auth and must never reach prod.
2. AsyncPostgresSaver pool runs with `prepare_threshold=0` and `autocommit=True`.
   prepare_threshold=0 stops psycopg3 from registering prepared statements,
   which would otherwise pin connections inside RDS Proxy and exhaust the
   t3.micro's ~85-connection cap. autocommit=True is required by .setup().

Lambda flush: every exit path calls flush_and_shutdown(lambda_sleep=True).
The 15s sleep is the documented Langfuse drain window — without it ~30% of
traces are lost when Lambda freezes the container before the SDK's
background HTTP sender finishes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level so warm Lambda invocations reuse the pool + checkpointer.
_pool: Any = None
_checkpointer: Any = None


def _check_security_invariants() -> None:
    from dossier.core.settings import get_settings  # noqa: PLC0415
    s = get_settings()
    if s.dossier_auth_dev_bypass and s.is_lambda_runtime:
        raise RuntimeError(
            "SECURITY: DOSSIER_AUTH_DEV_BYPASS must not be set when running as a "
            "deployed Lambda. Unset it before deploying."
        )


async def _get_checkpointer() -> Any:
    global _pool, _checkpointer

    # Deferred so the api-lambda image (which doesn't bundle psycopg_pool)
    # can still import this module.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    if _pool is None:
        from dossier.core.settings import get_settings  # noqa: PLC0415

        _pool = AsyncConnectionPool(
            conninfo=get_settings().database_url_libpq,
            min_size=0,  # psycopg_pool >=3.2 defaults to 4; pin ≤ max_size or init raises
            max_size=2,
            kwargs={
                "autocommit": True,       # required by AsyncPostgresSaver.setup()
                "prepare_threshold": 0,   # stops RDS Proxy from pinning the connection
            },
            open=False,  # defer .open() to the runtime asyncio loop
        )
        await _pool.open()
        _checkpointer = AsyncPostgresSaver(_pool)
        await _checkpointer.setup()  # idempotent
        logger.info("runner: AsyncPostgresSaver pool initialized")

    return _checkpointer


async def run_graph(investigation_id: str) -> None:
    """Run the investigation graph. Resumes from checkpoint if interrupted."""
    from dossier.core.db import get_async_session
    from dossier.investigate import repository as repo
    from dossier.investigate.graph import build_graph
    from dossier.observability import (
        flush_and_shutdown,
        get_langchain_callback_handler,
        get_langfuse_client,
    )

    langfuse_client = get_langfuse_client(strict=False)

    from dossier.investigate.input_ref import InvestigationInput  # noqa: PLC0415
    async with get_async_session() as session:
        row = await repo.aget_investigation_metadata(session, investigation_id)

    if not row:
        logger.error("runner: investigation_id=%s not found", investigation_id)
        if langfuse_client:
            flush_and_shutdown(langfuse_client, lambda_sleep=True)
        return

    input_type, input_ref, langfuse_trace_id = row
    parsed = InvestigationInput.parse(input_ref)
    company_name = parsed.value.strip() or "unknown"
    context_hint = parsed.context_hint.strip() if parsed.context_hint else None
    input_url = parsed.value.strip() if input_type == "url" else None

    checkpointer = await _get_checkpointer()
    graph = build_graph(checkpointer=checkpointer)

    lf_handler = get_langchain_callback_handler(
        trace_id=langfuse_trace_id,
        session_id=str(investigation_id),
        strict=False,
    )

    config: dict[str, Any] = {
        "configurable": {"thread_id": str(investigation_id)},
        "callbacks": [lf_handler] if lf_handler is not None else [],
        # Langfuse 4.x reads session_id from run metadata, not the constructor.
        "metadata": {"langfuse_session_id": str(investigation_id)},
    }

    # ainvoke semantics: dict input → re-run from START; None → continue from
    # the last checkpoint. An interrupted thread has non-empty `next` and a
    # non-None created_at — that's how we tell resume from cold start.
    existing_state = await graph.aget_state(config)
    is_resume = bool(existing_state.next) and existing_state.created_at is not None

    if is_resume:
        logger.info(
            "runner: resuming investigation_id=%s from checkpoint (next=%s)",
            investigation_id, existing_state.next,
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
        # Fresh session — earlier one may be in an aborted-transaction state.
        try:
            async with get_async_session() as session:
                async with session.begin():
                    await repo.aupdate_status(session, investigation_id, "failed")
        except Exception:
            logger.exception(
                "runner: failed to mark investigation_id=%s as failed", investigation_id
            )
        raise
    finally:
        if langfuse_client:
            flush_and_shutdown(langfuse_client, lambda_sleep=True)


def handler(event: dict, context: Any) -> dict:
    """AWS Lambda entry point. Event: {"investigation_id": "<uuid>"}."""
    _check_security_invariants()

    investigation_id = event.get("investigation_id")
    if not investigation_id:
        logger.error("runner.handler: missing investigation_id in event")
        return {"statusCode": 400, "body": "missing investigation_id"}

    asyncio.run(run_graph(str(investigation_id)))
    return {"statusCode": 200, "body": "ok"}


__all__ = ["handler", "run_graph"]
