from __future__ import annotations
# Each section has three names: field (Pydantic / synthesizer output),
# db_name (claims.section column), heading (rendered markdown).

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

FIELD_TO_DB: dict[str, str] = {s.field: s.db_name for s in BRIEF_SECTIONS}
DB_TO_FIELD: dict[str, str] = {s.db_name: s.field for s in BRIEF_SECTIONS}

BRIEF_FIELDS: tuple[str, ...] = tuple(s.field for s in BRIEF_SECTIONS)
BRIEF_DB_SECTIONS: tuple[str, ...] = tuple(s.db_name for s in BRIEF_SECTIONS)


def section_headings() -> tuple[tuple[str, str], ...]:
    return tuple((s.heading, s.field) for s in BRIEF_SECTIONS)


class BriefClaim(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claim_text: str
    quoted_span: str
    source_chunk_id: str


class Brief(BaseModel):
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
    # Missing fields default to []; new sections only need to touch BRIEF_SECTIONS above.
    return Brief(**{field: grouped.get(field, []) for field in BRIEF_FIELDS})

