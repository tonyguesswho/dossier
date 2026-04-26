"""RAG chat — top-k retrieve, Sonnet answer, citation resolution.

Per turn:
  retrieve top-k → (widen-search if retrieval is weak) → call Sonnet with
  ChatAnswer schema → replace [S:<chunk_id>] markers with markdown links →
  persist user + assistant turns atomically.

Widen-search fires a fresh Exa query when the top retrieved chunk's cosine
distance exceeds WEAK_RETRIEVAL_DISTANCE. The query is always prefixed with
the investigation subject — without that scoping, a generic question like
"where is HQ" matches headquarters pages for any famous company and
permanently pollutes the corpus.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.core.llm import STRONG_MODEL_ID, strong_model
from dossier.investigate.retrieve import RetrievedChunk, retrieve_top_k

logger = logging.getLogger(__name__)


DEFAULT_CHAT_TOP_K: int = 6


class ChatAnswer(BaseModel):
    """extra='forbid' so OpenAI structured-output compiles a strict schema."""
    model_config = ConfigDict(extra="forbid")
    answer_text: str
    cited_chunk_ids: list[str]


_SYSTEM_PROMPT_TEMPLATE = """You are an analyst answering a VC's question about \
{subject_line}.

You MUST answer using ONLY the provided source chunks, which are marked
`[S:<chunk_id>]`. For every factual claim in your answer, cite at least one
chunk inline using the exact pattern `[S:<chunk_id>]`. If the chunks don't
contain information to answer the question, say so plainly — DO NOT invent
facts or cite chunks you didn't use.

SUBJECT DISCIPLINE: The user is asking about {subject}. If a source chunk is
clearly about a DIFFERENT company or entity (e.g., a chunk about Microsoft's
HQ when the subject is Andela, or a chunk about Flutterwave's team when the
subject is Andela), you MUST ignore that chunk and NOT cite it — even if it
appears semantically relevant to the question text. If this filtering leaves
no usable chunks, answer: "The provided sources don't contain information
about that specific aspect of {subject}." Do not answer off-subject.

Return `answer_text` (the prose answer with inline [S:xxx] markers) and
`cited_chunk_ids` (the unique chunk_ids you actually referenced)."""


def _build_system_prompt(subject: str | None) -> str:
    subject_line = subject if subject else "a company"
    return _SYSTEM_PROMPT_TEMPLATE.format(
        subject=subject or "this company",
        subject_line=subject_line,
    )


def _build_user_prompt(question: str, retrieved: list[RetrievedChunk]) -> str:
    chunks_md = "\n\n".join(
        f"[S:{r.chunk_id}] ({r.url})\n{r.text}" for r in retrieved
    )
    return f"Question: {question}\n\nSource chunks:\n\n{chunks_md}"


def answer_with_citations(
    question: str,
    retrieved: list[RetrievedChunk],
    *,
    subject: str | None = None,
    client: Any = None,
) -> ChatAnswer:
    """Sonnet structured-output call. Fail-open on parsed=None — a malformed
    chat response degrades to a visible apology, never 500s the request.
    """
    active_client = client if client is not None else strong_model()
    completion = active_client.beta.chat.completions.parse(
        model=STRONG_MODEL_ID,
        messages=[
            {"role": "system", "content": _build_system_prompt(subject)},
            {"role": "user", "content": _build_user_prompt(question, retrieved)},
        ],
        response_format=ChatAnswer,
    )
    parsed = completion.choices[0].message.parsed
    if parsed is None:
        logger.warning("chat: parsed=None; returning empty answer")
        return ChatAnswer(
            answer_text="I couldn't generate an answer.",
            cited_chunk_ids=[],
        )
    return parsed


_CITATION_RE = re.compile(r"\[S:([^\]]+)\]")


def resolve_citations(answer_text: str, url_by_chunk: dict[str, str]) -> str:
    """Replace [S:<chunk_id>] markers with `([source](url))`. Unknown ids fall
    back to literal `(source)` so a hallucinated id doesn't render a broken link.
    """

    def _replace(match: re.Match[str]) -> str:
        chunk_id = match.group(1).strip()
        url = url_by_chunk.get(chunk_id)
        return f"([source]({url}))" if url else "(source)"

    return _CITATION_RE.sub(_replace, answer_text)


def _load_url_by_chunk(conn, investigation_id: UUID) -> dict[str, str]:
    url_rows = conn.execute(
        text(
            "SELECT sc.id::text AS chunk_id, s.url AS url "
            "FROM source_chunks sc "
            "JOIN sources s ON sc.source_id = s.id "
            "WHERE s.investigation_id = :inv_id"
        ),
        {"inv_id": str(investigation_id)},
    ).all()
    return {r.chunk_id: r.url for r in url_rows}


# Tuned for text-embedding-3-small: in-corpus matches usually land at
# 0.15–0.35; 0.4+ typically means we don't have what the user asked about.
WEAK_RETRIEVAL_DISTANCE = 0.4


def _investigation_subject(eng: Engine, investigation_id: UUID) -> str | None:
    """Pull input_ref, return the bare subject (drops any context hint)."""
    from dossier.investigate.input_ref import InvestigationInput  # noqa: PLC0415
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT input_ref FROM investigations WHERE id = :id"),
            {"id": str(investigation_id)},
        ).fetchone()
    if row is None or not row[0]:
        return None
    subject = InvestigationInput.parse(row[0]).value.strip()
    return subject or None


def _widen_search(
    investigation_id: UUID,
    question: str,
    eng: Engine,
) -> int:
    """Fresh subject-scoped Exa search; ingest into this investigation. Never
    raises — chat must complete with whatever the original retrieval produced.

    Subject scoping is critical: a generic "where is HQ" embedded alone matches
    headquarters pages for any famous company. Always prefix with the subject
    so Exa returns pages about THIS company.
    """
    try:
        from dossier.investigate.tools import exa as exa_tool
        from dossier.investigate.ingest import ingest_tool_results
        from dossier.investigate.tools.types import ToolResult
    except Exception:
        logger.exception("chat: widen-search imports failed")
        return 0

    subject = _investigation_subject(eng, investigation_id)
    if not subject:
        # Refuse to widen without a subject — would pollute the corpus with
        # whatever Exa thinks the question is about.
        logger.warning("chat: widen-search skipped — no subject found")
        return 0

    scoped_query = f"{subject}: {question}"
    logger.info("chat: widen-search query=%r", scoped_query[:120])

    try:
        new_results = exa_tool.search(scoped_query, num_results=3)
    except Exception:
        logger.warning("chat: widen Exa failed", exc_info=True)
        return 0

    if not new_results:
        return 0

    # Tag widened chunks so audits can find them and a cleanup query can prune
    # corpus pollution if the question was off-target despite the subject.
    tagged_results = [
        ToolResult(
            url=r.url,
            source_kind=r.source_kind,
            text=r.text,
            title=r.title,
            fetched_at=r.fetched_at,
            raw_metadata={
                **r.raw_metadata,
                "widened_from_chat": True,
                "widen_subject": subject,
                "widen_question": question[:200],
            },
        )
        for r in new_results
    ]

    try:
        ingest_tool_results(investigation_id, tagged_results, engine=eng)
    except Exception:
        logger.exception("chat: widen-ingest failed")
        return 0

    return len(tagged_results)


def run_chat_turn(
    investigation_id: UUID,
    question: str,
    *,
    engine: Engine | None = None,
    client: Any = None,
) -> tuple[str, list[str]]:
    """retrieve → widen if weak → synthesize → resolve → persist.

    Both turns persist in one transaction so a mid-write crash can't leave
    an orphan user turn that the history endpoint would render as a
    dangling question.
    """
    eng = engine if engine is not None else get_engine()

    subject = _investigation_subject(eng, investigation_id)

    retrieved = retrieve_top_k(investigation_id, question, k=DEFAULT_CHAT_TOP_K)
    top_distance = retrieved[0].distance if retrieved else None
    is_weak = (not retrieved) or (top_distance is not None and top_distance > WEAK_RETRIEVAL_DISTANCE)

    widened_count = 0
    if is_weak:
        logger.info(
            "chat: weak retrieval (top_distance=%s) — widening for %r",
            top_distance, question[:80],
        )
        widened_count = _widen_search(investigation_id, question, eng)
        if widened_count > 0:
            retrieved = retrieve_top_k(investigation_id, question, k=DEFAULT_CHAT_TOP_K)

    if not retrieved:
        empty_reply = "I don't have any source material for this investigation yet."
        _persist_turn_pair(eng, investigation_id, question, empty_reply, [])
        return empty_reply, []

    answer = answer_with_citations(
        question, retrieved, subject=subject, client=client
    )

    with eng.connect() as conn:
        url_by_chunk = _load_url_by_chunk(conn, investigation_id)
    resolved = resolve_citations(answer.answer_text, url_by_chunk)

    _persist_turn_pair(
        eng, investigation_id, question, resolved, answer.cited_chunk_ids,
    )

    return resolved, answer.cited_chunk_ids


def _persist_turn_pair(
    eng: Engine,
    investigation_id: UUID,
    user_question: str,
    assistant_content: str,
    cited_chunk_ids: list[str],
) -> None:
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_messages (investigation_id, role, content) "
                "VALUES (:i, 'user', :q)"
            ),
            {"i": str(investigation_id), "q": user_question},
        )
        conn.execute(
            text(
                "INSERT INTO chat_messages "
                "(investigation_id, role, content, cited_chunk_ids) "
                "VALUES (:i, 'assistant', :c, CAST(:cids AS JSONB))"
            ),
            {
                "i": str(investigation_id),
                "c": assistant_content,
                "cids": json.dumps(cited_chunk_ids),
            },
        )


__all__ = [
    "ChatAnswer",
    "DEFAULT_CHAT_TOP_K",
    "answer_with_citations",
    "resolve_citations",
    "run_chat_turn",
]
