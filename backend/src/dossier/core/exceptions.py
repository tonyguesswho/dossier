"""Pipeline-level exception types."""
from __future__ import annotations


class PipelineError(RuntimeError):
    """Raised when an investigation pipeline step cannot continue.

    Caught by the graph runner, which writes investigations.status='failed'
    and investigations.error=str(exc).
    """
