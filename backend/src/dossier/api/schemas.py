"""Pydantic request/response bodies for the investigations API.

These are the contract the Next.js route handlers (Wave 3) talk to. FastAPI
auto-validates request bodies via these classes — malformed input returns 422
without touching the route handler body.

Validation rules derive from:
  - UI-SPEC §3 Form (maxLength 200 on company/url, optional context_hint).
  - CONTEXT.md D-27 (reject non-http(s) URLs, obvious injection substrings).
  - GUARD-03 REQUIREMENTS (rate limit + input validation at investigation kickoff).

Rejected alternatives:
  - Split name/url into two endpoints: two pages of routing for one product affordance.
  - Skip max_length on context_hint: LLM prompts can grow unboundedly — 500 chars is
    a reasonable ceiling that matches a tweet-length research context.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Obvious injection-attempt substrings rejected by the POST validator (D-27).
# This list is defense-in-depth; the main defense is parameterized SQL + GUARD-01 sandbox.
_REJECT_SUBSTRINGS: tuple[str, ...] = (
    "\x00",        # null byte
    "`",           # backtick (shell/SQL injection marker)
    "<script",     # XSS
    "DROP TABLE",  # classic SQL injection signature
    "ignore previous",     # common prompt-injection phrase
    "ignore all previous", # variant
)


_TLD_PATTERN = re.compile(r"\.(com|io|ai|co|net|org|app|dev)(/|$)", re.IGNORECASE)


def _is_url_like(value: str) -> bool:
    return "://" in value or bool(_TLD_PATTERN.search(value))


def _auto_prefix_https(value: str) -> str:
    """Match UI-SPEC §3 behavior: auto-prefix https:// if TLD-pattern present and no scheme."""
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
    context_hint: Optional[str] = Field(default=None, max_length=500)

    @field_validator("value")
    @classmethod
    def _check_value(cls, v: str) -> str:
        _check_injection_patterns(v)
        # Disallow raw newlines in the input field — prevents HINT_SEPARATOR smuggling.
        if "\n" in v or "\r" in v:
            raise ValueError("guardrail_rejected")
        return v

    @field_validator("context_hint")
    @classmethod
    def _check_hint(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        _check_injection_patterns(v)
        return v

    def normalized_value(self) -> str:
        """Return the input_ref value portion (pre-HINT_SEPARATOR)."""
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
    """Same shape as create — re-run returns the id of the NEW investigation."""


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
    display_name: str  # company name/URL label sourced from investigations.input_ref (hint stripped)
    sources_count: int = 0
    claims_count: int = 0
    error: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None


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


class InvestigationBriefResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str
    status: str
    brief_markdown: str
    sources: list[SourceListItem]
    started_at: datetime
    completed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Phase 6-lite chat (Plan 03-14)
# ---------------------------------------------------------------------------

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
        # Same substring denylist as CreateInvestigationBody — chat questions are
        # another prompt-injection surface (Plan 03-08 classifier protects
        # retrieved content at ingest, but the question itself also flows to
        # Sonnet and must be filtered for the obvious attack phrases).
        _check_injection_patterns(v)
        return v


class ChatTurnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    answer: str
    cited_chunk_ids: list[str]


__all__ = [
    "ChatHistoryResponse",
    "ChatMessageItem",
    "ChatTurnBody",
    "ChatTurnResponse",
    "CreateInvestigationBody",
    "CreateInvestigationResponse",
    "InvestigationBriefResponse",
    "InvestigationListItem",
    "InvestigationListResponse",
    "InvestigationStatusResponse",
    "ReRunResponse",
    "RenameInvestigationBody",
    "SourceListItem",
]
