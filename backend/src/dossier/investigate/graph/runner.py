from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level so warm Lambda invocations reuse the pool + checkpointer.
# _event_loop is also cached: asyncio.run() destroys its loop after each call,
# which invalidates the asyncio.Lock objects inside AsyncConnectionPool and causes
# "bound to a different event loop" on the second warm invocation.
_pool: Any = None
_checkpointer: Any = None
_event_loop: asyncio.AbstractEventLoop | None = None


def _get_event_loop() -> asyncio.AbstractEventLoop:
    """Return a persistent event loop, creating one if the current one is closed."""
    global _event_loop, _pool, _checkpointer
    if _event_loop is None or _event_loop.is_closed():
        _event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_event_loop)
        # The pool/checkpointer were bound to the old loop — must re-initialise.
        _pool = None
        _checkpointer = None
    return _event_loop


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

    # Deferred so the api-lambda image (no psycopg_pool) can still import this module.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    if _pool is None:
        from dossier.core.settings import get_settings  # noqa: PLC0415

        _pool = AsyncConnectionPool(
            conninfo=get_settings().database_url_libpq,
            min_size=0,  # psycopg_pool >=3.2 defaults to 4; must be ≤ max_size
            max_size=2,
            kwargs={
                "autocommit": True,       # required by AsyncPostgresSaver.setup()
                "prepare_threshold": 0,   # stops RDS Proxy from pinning the connection
            },
            open=False,  # defer .open() to the runtime asyncio loop
        )
        await _pool.open()
        _checkpointer = AsyncPostgresSaver(_pool)
        await _checkpointer.setup()
        logger.info("runner: AsyncPostgresSaver pool initialized")

    return _checkpointer


async def run_graph(investigation_id: str) -> None:
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

    # Resume detection: interrupted threads have non-empty `next` AND non-None created_at.
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
        # Fresh session — the earlier one may be in an aborted-transaction state.
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


def run_graph_sync(investigation_id: str) -> None:
    """Synchronous wrapper safe to call from BackgroundTask threads and Lambda handler."""
    loop = _get_event_loop()
    if loop.is_running():
        # Called from a worker thread while the loop runs on the main thread (e.g.,
        # Starlette BackgroundTask via anyio). run_until_complete() raises "already running".
        # run_coroutine_threadsafe schedules the coroutine on the live loop and blocks
        # this thread until done — anyio yields control back so the loop can process it.
        future = asyncio.run_coroutine_threadsafe(run_graph(investigation_id), loop)
        future.result()
    else:
        loop.run_until_complete(run_graph(investigation_id))


def handler(event: dict, context: Any) -> dict:
    # AWS Lambda entry. Event: {"investigation_id": "<uuid>"}.
    _check_security_invariants()

    investigation_id = event.get("investigation_id")
    if not investigation_id:
        logger.error("runner.handler: missing investigation_id in event")
        return {"statusCode": 400, "body": "missing investigation_id"}

    _get_event_loop().run_until_complete(run_graph(str(investigation_id)))
    return {"statusCode": 200, "body": "ok"}


__all__ = ["handler", "run_graph"]
