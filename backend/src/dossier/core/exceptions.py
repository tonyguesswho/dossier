"""Pipeline-level exception types shared across investigate/* modules.

Rejected alternatives:
  - Raise bare RuntimeError: loses the "this is a pipeline-stage failure" signal.
  - Use Pydantic ValidationError directly: conflates schema errors with higher-level
    pipeline failures (tool outages, empty corpus, etc.).
"""
from __future__ import annotations


class PipelineError(RuntimeError):
    """Raised when an investigation pipeline step cannot continue.

    Examples (CONTEXT.md D-05, §Claude's Discretion):
      - Synthesizer returned malformed JSON that cannot parse into `Brief`.
      - Required env var is missing at runtime (OPENROUTER_API_KEY).
      - All three search tools returned zero results (fail-closed variant).

    Caught by the graph's finalize node (or by run_graph's outer try/except)
    which writes `investigations.status = 'failed'` and
    `investigations.error = str(exc)`.
    """
