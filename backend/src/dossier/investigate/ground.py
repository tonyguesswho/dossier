"""Substring-match BriefClaims against retrieved chunks; INSERT into claims.

Best-effort grounding: claims that don't substring-match are still persisted
with grounded_source_chunk_id=NULL so the hallucination metric can count them.

Normalization is shared with the eval scorer via
`dossier.core.text_normalize` — both call the same function so eval-time
and grounding-time scores can't drift.
"""
from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.core.text_normalize import normalize
from dossier.investigate.brief_schema import FIELD_TO_DB, Brief, BriefClaim
from dossier.investigate.retrieve import RetrievedChunk

logger = logging.getLogger(__name__)


class GroundStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    claims_written: int = 0
    claims_grounded: int = 0
    claims_unmatched: int = 0
    claims_unknown_source: int = 0


def _locate_span(chunk_text: str, quoted_span: str) -> tuple[int | None, int | None]:
    """Best-effort char offsets — exact case first, then case-insensitive.
    Returns (None, None) when neither match. The normalized comparison may
    succeed where the raw locate doesn't (whitespace collapse).
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
    """Ground each BriefClaim to a source chunk (or NULL) and persist.

    Per claim:
      missing chunk          → INSERT with NULL grounded_source_chunk_id
      normalized quote in chunk → INSERT with chunk + source-absolute span
      otherwise              → INSERT with NULL

    All rows count toward claims_written so the hallucination metric is
    computed against every claim the synthesizer emitted.
    """
    stats = GroundStats()
    eng = engine if engine is not None else get_engine()

    chunks_by_id: dict[str, RetrievedChunk] = {
        str(c.chunk_id): c for c in retrieved
    }

    brief_dict = brief.model_dump()

    with eng.begin() as conn:
        for field_name, db_section in FIELD_TO_DB.items():
            section_claims: list[dict] = brief_dict.get(field_name, []) or []
            for ordinal, claim_dict in enumerate(section_claims):
                claim = BriefClaim(**claim_dict)
                grounded_id: str | None = None
                grounded_start: int | None = None
                grounded_end: int | None = None

                chunk = chunks_by_id.get(claim.source_chunk_id)
                if chunk is None:
                    stats.claims_unknown_source += 1
                    logger.info(
                        "Claim cites unknown source_chunk_id=%s — storing NULL gid",
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
                            # Source-absolute = chunk's source offset + local match.
                            grounded_start = chunk.char_start + loc_start
                            grounded_end = chunk.char_start + loc_end
                        else:
                            # Normalized match but raw locate failed (whitespace
                            # drift) — fall back to the full chunk span so the UI
                            # can still highlight something.
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
                        "gid": grounded_id,
                        "gs": grounded_start,
                        "ge": grounded_end,
                        "ord": ordinal,
                    },
                )
                stats.claims_written += 1

    return stats


__all__ = [
    "GroundStats",
    "ground_claims",
]
