"""ToolResult contract — uniform shape across Exa + GitHub + Firecrawl.

Every tool returns list[ToolResult]; pipeline.py concatenates and hands off
to ingest.py (which chunks + embeds + writes to source_chunks via the shared
sources table). source_kind must match the sources.source_kind CHECK constraint
values from migration 0001 lines 130-131.

Rejected alternatives:
  - Return raw dicts from each tool: loses type-checking; ingest.py would have to
    know three different shapes.
  - One Pydantic subclass per tool: over-engineered for three tools; normalizing
    to one shape is the point of the wrapper layer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# source_kind values emitted by the Phase 2 + Phase 3 + Phase 5-lite tools.
# Widened by Plan 03-04: NewsAPI ('news') and Crunchbase ('crunchbase') join
# the Phase 2 trio ('web', 'github', 'crawl'). Widened by Plan 03-13: 'deck_page'
# for pitch-deck upload ingestion (MarkItDown-converted PDF). The
# sources.source_kind CHECK constraint in migration 0001 already permits all
# six values — no DB change needed here.
ToolSourceKind = Literal["web", "github", "crawl", "news", "crunchbase", "deck_page"]


class ToolResult(BaseModel):
    """One result from an external tool — uniform across Exa/GitHub/Firecrawl."""

    model_config = ConfigDict(from_attributes=True)

    url: str
    source_kind: ToolSourceKind
    text: str
    title: Optional[str] = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["ToolResult", "ToolSourceKind"]
