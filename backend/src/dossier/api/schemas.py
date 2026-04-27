from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Front-door denylist. Defense in depth — real defenses are parameterized SQL + the sandbox.
_REJECT_SUBSTRINGS: tuple[str, ...] = (
    "\x00",
    "`",
    "<script",
    "DROP TABLE",
    "ignore previous",
    "ignore all previous",
)


_TLD_PATTERN = re.compile(r"\.(com|io|ai|co|net|org|app|dev)(/|$)", re.IGNORECASE)


def _is_url_like(value: str) -> bool:
    return "://" in value or bool(_TLD_PATTERN.search(value))


def _auto_prefix_https(value: str) -> str:
    if "://" not in value and _TLD_PATTERN.search(value):
        return "https://" + value
    return value


def _validate_url_scheme(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url_scheme_rejected")


def _check_injection_patterns(value: str) -> None:
    lowered = value.lower()
    for bad in _REJECT_SUBSTRINGS:
        if bad.lower() in lowered:
            raise ValueError("guardrail_rejected")


class CreateInvestigationBody(BaseModel):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    kind: Literal["name", "url"]
    value: str = Field(min_length=1, max_length=200)
    context_hint: str | None = Field(default=None, max_length=500)

    @field_validator("value")
    @classmethod
    def _check_value(cls, v: str) -> str:
        _check_injection_patterns(v)
        # Newlines could smuggle a fake hint separator into InvestigationInput encoding.
        if "\n" in v or "\r" in v:
            raise ValueError("guardrail_rejected")
        return v

    @field_validator("context_hint")
    @classmethod
    def _check_hint(cls, v: str | None) -> str | None:
        if v is None:
            return None
        _check_injection_patterns(v)
        return v

    def normalized_value(self) -> str:
        if self.kind == "url":
            prefixed = _auto_prefix_https(self.value)
            _validate_url_scheme(prefixed)
            return prefixed
        return self.value


class CreateInvestigationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str


class ReRunResponse(CreateInvestigationResponse):
    pass


class RenameInvestigationBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=200)

    @field_validator("display_name")
    @classmethod
    def _check(cls, v: str) -> str:
        _check_injection_patterns(v)
        if "\n" in v or "\r" in v:
            raise ValueError("guardrail_rejected")
        return v


class InvestigationStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: str
    display_name: str
    sources_count: int = 0
    claims_count: int = 0
    error: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class SourceListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    url: str
    source_kind: str


class InvestigationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str
    status: str
    started_at: datetime


class InvestigationListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    items: list[InvestigationListItem]


class ScorecardResponse(BaseModel):
    """Eval scorecard surfaced on the brief."""
    model_config = ConfigDict(from_attributes=True)
    citation_precision: float
    grounding_rate: float
    total_claims: int
    grounded_claims: int


class InvestigationBriefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str
    status: str
    brief_markdown: str
    sources: list[SourceListItem]
    started_at: datetime
    completed_at: datetime | None = None
    scorecard: ScorecardResponse | None = None


class ChatMessageItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    role: Literal["user", "assistant"]
    content: str
    cited_chunk_ids: list[str] = []
    created_at: datetime


class ChatHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    investigation_id: UUID
    messages: list[ChatMessageItem]


class ChatTurnBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def _check(cls, v: str) -> str:
        # Chat questions reach the synthesizer too — same denylist as the create body.
        _check_injection_patterns(v)
        return v


class ChatTurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    answer: str
    cited_chunk_ids: list[str]

