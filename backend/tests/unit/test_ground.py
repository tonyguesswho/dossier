"""Unit tests for dossier.investigate.ground.

Locked by 02-CONTEXT.md D-06 (NULL for unmatched), D-07 (same normalize rule
as scorer), D-08 (substring-only).

Tests use a fake engine that captures execute() parameter dicts — no real DB.
D-07 contract verified by: `from dossier.eval.scorer import normalize` is the
same object ground.py imports.

Target runtime: <200ms.
"""
from __future__ import annotations

from uuid import UUID, uuid4

from dossier.eval.scorer import normalize as scorer_normalize
from dossier.investigate.ground import (
    SECTION_FIELD_TO_DB,
    ground_claims,
)
from dossier.investigate.retrieve import RetrievedChunk
from dossier.models import Brief, BriefClaim


# ---------------------------------------------------------------------------
# D-07 contract lock: ground and scorer MUST share the function object.
# ---------------------------------------------------------------------------
def test_ground_normalize_is_same_object_as_scorer_normalize() -> None:
    from dossier.investigate import ground as g

    assert g.normalize is scorer_normalize


class _FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def execute(self, sql, params):
        self.executed.append((str(sql), dict(params)))

        class _R:
            def fetchone(self):
                return None

        return _R()


class _FakeEngine:
    def __init__(self) -> None:
        self.conn = _FakeConn()

    def begin(self):
        engine = self

        class _Ctx:
            def __enter__(self_inner):
                return engine.conn

            def __exit__(self_inner, *a):
                return False

        return _Ctx()


def _chunk(chunk_id: UUID, text_val: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=uuid4(),
        url="https://x.com",
        text=text_val,
        char_start=100,  # arbitrary non-zero offset to verify additive math
        char_end=100 + len(text_val),
        distance=0.05,
    )


def _brief_with_one_claim_per_section(quoted_span: str, source_chunk_id: str) -> Brief:
    c = BriefClaim(
        claim_text="some claim",
        quoted_span=quoted_span,
        source_chunk_id=source_chunk_id,
    )
    return Brief(
        founders=[c],
        company=[c],
        market=[c],
        product=[c],
        risk_flags=[c],
        suggested_questions=[c],
    )


# ---------------------------------------------------------------------------
# Grounded claim: non-null gid + correct char offsets (additive from chunk.char_start)
# ---------------------------------------------------------------------------
def test_grounded_claim_gets_non_null_id_and_offsets() -> None:
    cid = uuid4()
    chunk = _chunk(cid, "Acme was founded in 2024 by Alice.")
    brief = _brief_with_one_claim_per_section(
        quoted_span="founded in 2024",
        source_chunk_id=str(cid),
    )
    engine = _FakeEngine()
    stats = ground_claims(uuid4(), brief, [chunk], engine=engine)
    assert stats.claims_written == 6
    assert stats.claims_grounded == 6
    assert stats.claims_unmatched == 0

    for _sql, params in engine.conn.executed:
        assert params["gid"] == str(cid)
        assert params["gs"] is not None
        assert params["ge"] is not None
        # Offsets are chunk.char_start + relative find offset.
        assert params["gs"] == 100 + chunk.text.find("founded in 2024")


# ---------------------------------------------------------------------------
# Unmatched claim: same chunk, span not present → NULL gid + unmatched counter
# ---------------------------------------------------------------------------
def test_unmatched_claim_gets_null_gid() -> None:
    cid = uuid4()
    chunk = _chunk(cid, "Acme was founded in 2024 by Alice.")
    brief = _brief_with_one_claim_per_section(
        quoted_span="reached 50k MAU",  # not in chunk
        source_chunk_id=str(cid),
    )
    engine = _FakeEngine()
    stats = ground_claims(uuid4(), brief, [chunk], engine=engine)
    assert stats.claims_grounded == 0
    assert stats.claims_unmatched == 6
    assert stats.claims_written == 6
    for _sql, params in engine.conn.executed:
        assert params["gid"] is None
        assert params["gs"] is None
        assert params["ge"] is None


# ---------------------------------------------------------------------------
# Unknown source_chunk_id: chunk not in retrieved → NULL + unknown counter
# ---------------------------------------------------------------------------
def test_unknown_source_chunk_id_gets_null_and_counted() -> None:
    cid = uuid4()
    chunk = _chunk(cid, "Acme was founded in 2024.")
    # Brief cites a DIFFERENT chunk id
    brief = _brief_with_one_claim_per_section(
        quoted_span="founded in 2024",
        source_chunk_id="not-a-real-id",
    )
    engine = _FakeEngine()
    stats = ground_claims(uuid4(), brief, [chunk], engine=engine)
    assert stats.claims_grounded == 0
    assert stats.claims_unknown_source == 6
    # All rows still written (D-06) so Phase 4 hallucination_rate can count them.
    assert stats.claims_written == 6


# ---------------------------------------------------------------------------
# Normalized match: casing + whitespace drift still resolves
# ---------------------------------------------------------------------------
def test_normalized_match_via_case_and_whitespace() -> None:
    cid = uuid4()
    chunk = _chunk(cid, "Acme was founded in 2024 by Alice.")
    brief = _brief_with_one_claim_per_section(
        quoted_span="FOUNDED  in\t2024",
        source_chunk_id=str(cid),
    )
    engine = _FakeEngine()
    stats = ground_claims(uuid4(), brief, [chunk], engine=engine)
    assert stats.claims_grounded == 6


# ---------------------------------------------------------------------------
# Section-field → DB-section map: risk_flags (plural) → "risk" (singular Literal)
# ---------------------------------------------------------------------------
def test_section_field_to_db_map_matches_db_section_values() -> None:
    assert SECTION_FIELD_TO_DB["risk_flags"] == "risk"
    assert SECTION_FIELD_TO_DB["founders"] == "founders"
    assert SECTION_FIELD_TO_DB["suggested_questions"] == "suggested_questions"
