from __future__ import annotations
# source_kind values must match the sources.source_kind CHECK constraint (migration 0001).

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ToolSourceKind = Literal["web", "github", "crawl", "news", "crunchbase", "deck_page"]


class ToolResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    url: str
    source_kind: ToolSourceKind
    text: str
    title: Optional[str] = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

