"""Phase 1 Langfuse connectivity smoke test.

Satisfies Phase 1 success criterion #3 and PLAT-04 (Phase 1 scope).

What this does:
  1. Loads .env + validates LANGFUSE_* via `dossier.observability`.
  2. Instantiates the Langfuse client through the centralized module
     (env-before-import ordering is guaranteed there — see observability.py).
  3. Opens one trace via `start_as_current_observation(as_type="span", ...)`
     — the Langfuse 4.x primary observation API (was `start_as_current_span`
     in 3.x, renamed in 4.0 for the observation-centric data model).
  4. Uses `propagate_attributes` to set trace-level tags + trace name —
     replaces 3.x's imperative `update_current_trace(...)` call.
  5. Calls flush() + shutdown() via observability helper so the trace is
     delivered before exit.
  6. Prints the trace URL to stdout (per CONTEXT.md §specifics).

What this is NOT:
  - Not the Lambda flush pattern: Phase 3 passes `lambda_sleep=True` to
    `flush_and_shutdown()`. For local scripts the 15s sleep is not needed
    (STACK.md §2.6).
  - Not an LLM call: Phase 1 is off the LLM budget (CONTEXT.md §domain).
  - Not an agent trace: LangChain CallbackHandler integration is Phase 3.

Usage:
    cd backend
    uv run python -m dossier.scripts.langfuse_smoke
"""
from __future__ import annotations

import logging
import sys

from dossier.observability import (
    flush_and_shutdown,
    get_langfuse_client,
    read_langfuse_env,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    # Validate env upfront and surface a clean error message if keys are absent.
    try:
        _public, _secret, host = read_langfuse_env(strict=True)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    client = get_langfuse_client(strict=True)

    # Deferred import: propagate_attributes belongs to the langfuse package;
    # importing it here (after get_langfuse_client) avoids any ordering concerns.
    from langfuse import propagate_attributes  # noqa: PLC0415

    # Langfuse 4.x: set trace-level attributes via propagate_attributes
    # (replaces 3.x's client.update_current_trace). All observations created
    # inside the `with` inherit the tags + trace_name.
    with propagate_attributes(
        tags=["phase-1", "smoke-test"],
        trace_name="dossier-smoke-test",
    ):
        with client.start_as_current_observation(
            as_type="span",
            name="phase-1-smoke",
            input={"event": "phase_1_setup_smoke_test"},
            metadata={
                "phase": "01-eval-harness-skeleton",
                "purpose": "PLAT-04 connectivity check",
                "host": host,
            },
        ) as span:
            # Set output at end of work. v4 infers trace-level I/O from the
            # root span automatically — no separate set_trace_io call needed
            # (that method is deprecated in 4.x).
            span.update(output={"status": "ok"})
            trace_url = client.get_trace_url()

    # Flush before shutdown so the HTTP post completes. Lambda callers
    # (Phase 3+) pass lambda_sleep=True.
    flush_and_shutdown(client, lambda_sleep=False)

    print("Langfuse trace delivered.")
    print(f"Trace URL: {trace_url}")
    print("Open the URL in a browser to confirm PLAT-04 (Phase 1 success criterion #3).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
