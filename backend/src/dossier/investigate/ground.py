"""Substring-match BriefClaims against retrieved chunks; INSERT into claims table.

Phase 2 grounding policy (CONTEXT.md D-06/D-07/D-08):
  - D-06: best-effort grounding — claims that cannot be substring-matched are
    still written with grounded_source_chunk_id=NULL so Phase 4's hallucination
    rate can count them.
  - D-07 contract: same normalization rule as Phase 1's citation_precision —
    lowercase + collapse whitespace + strip leading/trailing punctuation.
    Imported from dossier.eval.scorer via the public `normalize` alias.
    The contract is function-object identity: `ground.normalize is
    scorer.normalize` — enforced by test_ground_normalize_is_same_object_as_
    scorer_normalize. Drift here silently breaks Phase 4 eval.
  - D-08: substring-only — NO entailment check in Phase 2. Phase 4 adds
    entailment (Pitfall 1.2 paraphrase-as-citation defense).

Section-field mapping:
  - Brief Pydantic fields are synthesizer-facing slot names (plural risk_flags,
    plural suggested_questions). The claims.section DB column uses the
    BriefSection Literal values — singular `risk`. SECTION_FIELD_TO_DB handles
    the translation so tests and downstream readers see consistent DB values.

Rejected alternatives:
  - Re-implement _normalize locally: D-07 contract drift silently breaks Phase 4.
  - Refuse to insert unmatched claims: Phase 4's hallucination rate NEEDS the
    rows (with NULL grounded_source_chunk_id) to count unsupported claims.
  - Token-level fuzzy match (rapidfuzz/Levenshtein): masks paraphrase-as-citation
    (Pitfall 1.3) — scorer.py already explicitly rejected this.
  - SELECT + UPDATE flow: two round trips per claim. Single parameterized INSERT
    per claim matches the seed.py pattern and keeps the transaction compact.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine

# D-07 contract lock — SAME rule as scorer.citation_precision. Do NOT copy the
# normalize body into this module: the shared object is the contract.
from dossier.eval.scorer import normalize
from dossier.investigate.retrieve import RetrievedChunk
from dossier.models import Brief, BriefClaim

logger = logging.getLogger(__name__)


# Brief field name (synthesizer-facing) → DB claims.section value (BriefSection Literal).
# Note `risk_flags` (plural) field maps to `risk` (singular) DB section value.
SECTION_FIELD_TO_DB: dict[str, str] = {
    "founders": "founders",
    "company": "company",
    "market": "market",
    "product": "product",
    "risk_flags": "risk",
    "suggested_questions": "suggested_questions",
}


class GroundStats(BaseModel):
    """Counters returned from ground_claims for pipeline observability / tests."""

    model_config = ConfigDict(from_attributes=True)

    claims_written: int = 0
    claims_grounded: int = 0
    claims_unmatched: int = 0
    claims_unknown_source: int = 0


def _locate_span(chunk_text: str, quoted_span: str) -> tuple[int | None, int | None]:
    """Best-effort char offsets within chunk_text for the quoted_span.

    Tries exact-case first, then lowercase. Returns (start, end) on success;
    (None, None) if neither locate finds the span. Caller falls back to
    full-chunk span when the normalized comparison matched but the raw-text
    locate did not (e.g., whitespace collapse hid the raw anchor).
    """
    if not quoted_span:
        return None, None
    start = chunk_text.find(quoted_span)
    if start == -1:
        start = chunk_text.lower().find(quoted_span.lower())
    if start == -1:
        return None, None
    return start, start + len(quoted_span)


def ground_claims(
    investigation_id: UUID,
    brief: Brief,
    retrieved: list[RetrievedChunk],
    *,
    engine: Optional[Engine] = None,
) -> GroundStats:
    """Ground every BriefClaim to a source_chunk (or NULL) and INSERT into claims table.

    Contract (CONTEXT.md D-06/D-07/D-08):
      - For each BriefClaim: look up the cited chunk in `retrieved`.
      - If chunk missing → INSERT with NULL gid, increment claims_unknown_source.
      - If chunk present AND normalize(quoted_span) in normalize(chunk.text):
          INSERT with gid=chunk.chunk_id, span offsets absolute to source.
          Increment claims_grounded.
      - Else → INSERT with NULL gid, increment claims_unmatched.
      - All rows contribute to claims_written so Phase 4 hallucination_rate
        counts them uniformly.

    Returns GroundStats (counters only; DB writes happen inside a single
    engine.begin() transaction).
    """
    stats = GroundStats()
    eng = engine if engine is not None else get_engine()

    # str() the chunk_id so BriefClaim.source_chunk_id (str) can key the dict.
    chunks_by_id: dict[str, RetrievedChunk] = {
        str(c.chunk_id): c for c in retrieved
    }

    brief_dict = brief.model_dump()

    with eng.begin() as conn:
        for field_name, db_section in SECTION_FIELD_TO_DB.items():
            section_claims: list[dict] = brief_dict.get(field_name, []) or []
            for ordinal, claim_dict in enumerate(section_claims):
                claim = BriefClaim(**claim_dict)
                grounded_id: str | None = None
                grounded_start: int | None = None
                grounded_end: int | None = None

                chunk = chunks_by_id.get(claim.source_chunk_id)
                if chunk is None:
                    # Synthesizer cited a chunk id we don't have → record NULL.
                    stats.claims_unknown_source += 1
                    logger.info(
                        "Claim cites unknown source_chunk_id=%s "
                        "(stored with grounded_source_chunk_id=NULL per D-06)",
                        claim.source_chunk_id,
                    )
                else:
                    norm_q = normalize(claim.quoted_span)
                    norm_c = normalize(chunk.text)
                    if norm_q and norm_q in norm_c:
                        grounded_id = str(chunk.chunk_id)
                        loc_start, loc_end = _locate_span(
                            chunk.text, claim.quoted_span
                        )
                        if loc_start is not None and loc_end is not None:
                            # Absolute offsets in source.raw_text = chunk offset + local.
                            grounded_start = chunk.char_start + loc_start
                            grounded_end = chunk.char_start + loc_end
                        else:
                            # Normalized match but raw locate failed (whitespace
                            # collapse drift) — fall back to full-chunk span so
                            # Phase 4 UI can still highlight something meaningful.
                            grounded_start = chunk.char_start
                            grounded_end = chunk.char_end
                        stats.claims_grounded += 1
                    else:
                        stats.claims_unmatched += 1

                conn.execute(
                    text(
                        """
                        INSERT INTO claims
                            (investigation_id, section, claim_text,
                             grounded_source_chunk_id, grounded_span_start, grounded_span_end,
                             confidence, ordinal)
                        VALUES
                            (:inv, :sec, :txt,
                             CAST(:gid AS UUID), :gs, :ge,
                             NULL, :ord)
                        """
                    ),
                    {
                        "inv": str(investigation_id),
                        "sec": db_section,
                        "txt": claim.claim_text,
                        "gid": grounded_id,  # None → NULL via CAST on Postgres
                        "gs": grounded_start,
                        "ge": grounded_end,
                        "ord": ordinal,
                    },
                )
                stats.claims_written += 1

    return stats


__all__ = [
    "GroundStats",
    "SECTION_FIELD_TO_DB",
    "ground_claims",
    "normalize",  # re-exported for the D-07 contract-lock test
]
