"""Single-Lambda entry point for Dossier.

One container image, one handler. Dispatches on event shape:
  - {"investigation_id": "<uuid>"}  -> investigate path (runner.handler)
  - API Gateway / Function URL HTTP event  -> FastAPI via Mangum

Why one Lambda, not two (api-lambda + investigate-lambda split from
CLAUDE.md's "locked decisions"):
  The two-Lambda split is the correct production shape — api-lambda has
  a tight 30s timeout and no cold-start cost for polling; investigate-lambda
  carries the 900s timeout + heavier memory needed by LangGraph. For the
  2-day capstone demo we collapsed that into one image to keep the deploy
  story to a single `docker push` and one Terraform resource (see
  03-10-SUMMARY.md "Decisions and tradeoffs" #1). The event-shape dispatch
  below is the seam where the split will re-land: swap `if "investigation_id"
  in event` for a separate Lambda function and the FastAPI side of this file
  is untouched.

Why lifespan="off":
  FastAPI's startup/shutdown events don't fit the per-request Lambda model.
  Startup would run on every cold start (fine) but also expects a matching
  shutdown (never fires — Lambda freezes/thaws the process, it doesn't
  gracefully terminate). Turning lifespan off means we don't register
  resources via lifespan context managers; module-level globals (engine,
  langfuse client) are fine because they're lazy-initialized on first use.
"""
from __future__ import annotations

import logging
from typing import Any

from mangum import Mangum

from dossier.api.main import app
from dossier.investigate.graph import runner

logger = logging.getLogger(__name__)

# Mangum wraps the FastAPI ASGI app so Lambda HTTP events become ASGI calls.
# Constructed at module import time -> shared across warm invocations.
_mangum = Mangum(app, lifespan="off")


def handler(event: dict, context: Any) -> Any:
    """AWS Lambda entry point. Dispatches on event shape.

    The two event shapes never overlap:
      - Self-invoke payload: {"investigation_id": "..."} (no requestContext,
        no HTTP body — the API route handler we dispatch to doesn't look at
        `context`, it just reads investigation_id and runs the graph).
      - Function URL event: has `requestContext.http.method`, headers, body.
        Mangum unpacks that into an ASGI scope/receive/send pair.

    "investigation_id" is a safe selector because Mangum/Function URL events
    never put that key at the top level of the event dict.
    """
    if "investigation_id" in event:
        logger.info("lambda_handler: dispatching to runner.handler")
        return runner.handler(event, context)

    # Mangum's API Gateway v2 handler hard-requires `requestContext.http.sourceIp`
    # but Lambda Function URL events sometimes omit it (depends on runtime
    # version + IP-attribution config). Inject a sentinel so Mangum can unpack
    # the scope; the value is only used for ASGI scope.client[0] which our
    # FastAPI app doesn't read for routing.
    request_ctx = event.get("requestContext", {})
    http_ctx = request_ctx.get("http", {}) if isinstance(request_ctx, dict) else {}
    if isinstance(http_ctx, dict) and "sourceIp" not in http_ctx:
        http_ctx["sourceIp"] = "0.0.0.0"
        request_ctx["http"] = http_ctx
        event["requestContext"] = request_ctx

    logger.info("lambda_handler: dispatching to Mangum/FastAPI")
    return _mangum(event, context)


__all__ = ["handler"]
