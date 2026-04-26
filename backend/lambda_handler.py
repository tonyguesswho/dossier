"""Single-Lambda entry point. One container, one handler, dispatches on event shape:

    {"investigation_id": "..."} → runner.handler (graph run)
    Function URL HTTP event     → Mangum → FastAPI

The intended production shape is a two-Lambda split (thin api + heavy
investigate). Collapsed to one image for the demo to keep deployment to
a single `docker push`; the dispatch below is where the split will re-land.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from mangum import Mangum

from dossier.api.main import app
from dossier.investigate.graph import runner

logger = logging.getLogger(__name__)


def _ensure_event_loop() -> None:
    """Mangum 0.19 calls asyncio.get_event_loop(); Python 3.12+ raises
    RuntimeError when no loop is set on the main thread instead of auto-
    creating one. Lambda never primes a loop, so we do it here.
    """
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


# lifespan="off" because Lambda freezes/thaws the process — startup/shutdown
# events don't fit. Module-level globals (engine, langfuse) are lazy.
_mangum = Mangum(app, lifespan="off")


def handler(event: dict, context: Any) -> Any:
    """The two event shapes never overlap. Function URL events never carry
    `investigation_id` at the top level, which makes it a safe selector.
    """
    if "investigation_id" in event:
        logger.info("lambda_handler: dispatching to runner.handler")
        return runner.handler(event, context)

    # Mangum's API Gateway v2 handler hard-requires sourceIp, but Lambda
    # Function URL events sometimes omit it. Inject a sentinel so the ASGI
    # scope can unpack — our app doesn't read scope.client[0] for routing.
    request_ctx = event.get("requestContext", {})
    http_ctx = request_ctx.get("http", {}) if isinstance(request_ctx, dict) else {}
    if isinstance(http_ctx, dict) and "sourceIp" not in http_ctx:
        http_ctx["sourceIp"] = "0.0.0.0"
        request_ctx["http"] = http_ctx
        event["requestContext"] = request_ctx

    _ensure_event_loop()
    logger.info("lambda_handler: dispatching to Mangum/FastAPI")
    return _mangum(event, context)


__all__ = ["handler"]
