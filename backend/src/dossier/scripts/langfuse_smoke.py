"""Langfuse connectivity smoke test.

    cd backend && uv run python -m dossier.scripts.langfuse_smoke

Opens one trace via the Langfuse 4.x observation API. Lambda callers pass
lambda_sleep=True to flush_and_shutdown; for local scripts the 15s sleep
isn't needed.
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
    try:
        _public, _secret, host = read_langfuse_env(strict=True)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    client = get_langfuse_client(strict=True)

    # propagate_attributes belongs to the langfuse package — import after
    # get_langfuse_client to keep the env-before-import order intact.
    from langfuse import propagate_attributes  # noqa: PLC0415

    # 4.x: trace-level attributes via propagate_attributes (replaces 3.x's
    # imperative client.update_current_trace).
    with propagate_attributes(
        tags=["smoke-test"],
        trace_name="dossier-smoke-test",
    ):
        with client.start_as_current_observation(
            as_type="span",
            name="smoke",
            input={"event": "smoke_test"},
            metadata={"host": host},
        ) as span:
            span.update(output={"status": "ok"})
            trace_url = client.get_trace_url()

    flush_and_shutdown(client, lambda_sleep=False)

    print("Langfuse trace delivered.")
    print(f"Trace URL: {trace_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
