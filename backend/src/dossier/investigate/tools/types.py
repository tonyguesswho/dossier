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


# source_kind values that Phase 2 tools emit.
# The DB CHECK constraint (migration 0001) permits more ('news', 'crunchbase',
# 'deck_page') but Phase 2 only ships 'web', 'github', 'crawl'. Phase 3 widens
# when NewsAPI + Crunchbase + deck upload land.
ToolSourceKind = Literal["web", "github", "crawl"]


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
