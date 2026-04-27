from __future__ import annotations


class PipelineError(RuntimeError):
    # Caught by the graph runner, which writes investigations.status='failed' and investigations.error=str(exc).
    pass
