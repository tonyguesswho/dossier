"""Tests for ground.ground_claims — fake engine, no real DB."""
from __future__ import annotations

from uuid import UUID, uuid4

from dossier.investigate.ground import ground_claims
from dossier.investigate.retrieve import RetrievedChunk
from dossier.investigate.brief_schema import Brief, BriefClaim


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
        char_start=100,  # non-zero so additive offset math is verifiable
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
        assert params["gs"] == 100 + chunk.text.find("founded in 2024")


def test_unmatched_claim_gets_null_gid() -> None:
    """Span not present in cited chunk → NULL gid + unmatched counter."""
    cid = uuid4()
    chunk = _chunk(cid, "Acme was founded in 2024 by Alice.")
    brief = _brief_with_one_claim_per_section(
        quoted_span="reached 50k MAU",
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


def test_unknown_source_chunk_id_gets_null_and_counted() -> None:
    """Brief cites a chunk_id we don't have → NULL gid, still persisted so
    the hallucination metric can count it.
    """
    cid = uuid4()
    chunk = _chunk(cid, "Acme was founded in 2024.")
    brief = _brief_with_one_claim_per_section(
        quoted_span="founded in 2024",
        source_chunk_id="not-a-real-id",
    )
    engine = _FakeEngine()
    stats = ground_claims(uuid4(), brief, [chunk], engine=engine)
    assert stats.claims_grounded == 0
    assert stats.claims_unknown_source == 6
    assert stats.claims_written == 6


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
