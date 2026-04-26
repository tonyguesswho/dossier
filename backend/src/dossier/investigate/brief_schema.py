"""The Brief domain model — single source of truth for what a brief is.

A brief is a 6-section synthesizer output. Each section has three names:

  field    — Brief Pydantic field name, what the synthesizer's structured
             output produces ("risk_flags", "suggested_questions").
  db_name  — value stored in the claims.section column ("risk", "founders").
             Singular; matches the BriefSection Literal.
  heading  — display-cased heading used in the rendered markdown
             ("Risk Flags").

Adding a section means editing this file. Keep the synthesizer's prompt in
synthesize.py in sync — section *descriptions* live with the prompt because
they're content, not schema.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict


BriefSection = Literal[
    "founders",
    "company",
    "market",
    "product",
    "risk",
    "suggested_questions",
]


@dataclass(frozen=True, slots=True)
class _Section:
    field: str
    db_name: str
    heading: str


BRIEF_SECTIONS: tuple[_Section, ...] = (
    _Section("founders", "founders", "Founders"),
    _Section("company", "company", "Company"),
    _Section("market", "market", "Market"),
    _Section("product", "product", "Product"),
    _Section("risk_flags", "risk", "Risk Flags"),
    _Section("suggested_questions", "suggested_questions", "Suggested Questions"),
)

_BY_FIELD: dict[str, _Section] = {s.field: s for s in BRIEF_SECTIONS}
_BY_DB: dict[str, _Section] = {s.db_name: s for s in BRIEF_SECTIONS}

FIELD_TO_DB: dict[str, str] = {s.field: s.db_name for s in BRIEF_SECTIONS}
DB_TO_FIELD: dict[str, str] = {s.db_name: s.field for s in BRIEF_SECTIONS}

BRIEF_FIELDS: tuple[str, ...] = tuple(s.field for s in BRIEF_SECTIONS)
BRIEF_DB_SECTIONS: tuple[str, ...] = tuple(s.db_name for s in BRIEF_SECTIONS)


def field_to_db(field: str) -> str:
    return _BY_FIELD[field].db_name


def db_to_field(db_name: str) -> str:
    return _BY_DB[db_name].field


def section_headings() -> tuple[tuple[str, str], ...]:
    """(heading, field) pairs in canonical order — used by the markdown renderer."""
    return tuple((s.heading, s.field) for s in BRIEF_SECTIONS)


class BriefClaim(BaseModel):
    """Synthesizer output (pre-grounding). ground.py resolves it to a full
    Claim row with grounded_span_* via normalized-substring match. Ungrounded
    claims still get persisted with grounded_source_chunk_id=NULL so the
    hallucination metric can count them.
    """

    model_config = ConfigDict(from_attributes=True)

    claim_text: str
    quoted_span: str
    source_chunk_id: str


class Brief(BaseModel):
    """6-section synthesizer output, validated by openai's structured outputs.

    Field names are synthesizer-facing slot names ('risk_flags'); BriefSection
    Literal values are DB-facing ('risk'). Use FIELD_TO_DB / DB_TO_FIELD to
    translate between them.
    """

    model_config = ConfigDict(from_attributes=True)

    founders: list[BriefClaim]
    company: list[BriefClaim]
    market: list[BriefClaim]
    product: list[BriefClaim]
    risk_flags: list[BriefClaim]
    suggested_questions: list[BriefClaim]


def build_brief_from_grouped(
    grouped: dict[str, list[BriefClaim]],
) -> Brief:
    """Construct a Brief from a {field: list[BriefClaim]} dict.

    Missing fields default to []. Use this instead of hand-writing the
    constructor; new sections then only touch BRIEF_SECTIONS above.
    """
    return Brief(**{field: grouped.get(field, []) for field in BRIEF_FIELDS})


__all__ = [
    "BriefSection",
    "BriefClaim",
    "Brief",
    "BRIEF_SECTIONS",
    "BRIEF_FIELDS",
    "BRIEF_DB_SECTIONS",
    "FIELD_TO_DB",
    "DB_TO_FIELD",
    "field_to_db",
    "db_to_field",
    "section_headings",
    "build_brief_from_grouped",
]
