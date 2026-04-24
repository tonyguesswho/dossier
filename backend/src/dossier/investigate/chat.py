"""Basic RAG chat: top-k retrieve + Sonnet answer with citations.

Phase 6-lite demo scope (Plan 03-14). Non-streaming JSON round-trip per chat
turn. Full Phase 6 (intent router, widen-search sub-graph, SSE streaming,
click-claim-to-chat) deferred per 03-14-PLAN.md scope_note.

Pipeline per turn:
  1. retrieve_top_k(investigation_id, question, k=6) against source_chunks
     (scoped to sources.investigation_id — T-02-06-03 cross-investigation
     chunk-leak defense reused).
  2. Sonnet via strong_model() + .beta.chat.completions.parse with the
     ChatAnswer Pydantic schema. Mirrors synthesize.synthesize_brief so the
     structured-output guard-rails (refusal / parsed=None) are already proven.
  3. Post-process `[S:<chunk_id>]` markers to `([source](url))` using the same
     url_by_chunk lookup the brief renderer uses (finalize / ground.py).
  4. Persist the user turn + assistant turn atomically inside one engine.begin().

Rejected alternatives:
  - AsyncOpenAI rewrite: one-shot cheap call; not worth a parallel import surface.
    Matches Phase 2 synthesize pattern (sync openai SDK, no event-loop bridge
    needed because routes are sync FastAPI handlers — api-lambda is not the graph).
  - Streaming (SSE): Phase 6-full scope; demo-lite uses plain JSON so the
    frontend can reuse the existing fetch/useQuery stack instead of wiring
    fetch-event-source + SSE parsing.
  - Claim-grounding re-use (quoted_span verbatim-match): chat answers are prose
    synthesis, not enumerated claims. Citation precision is enforced by the
    `[S:<chunk_id>]` → url resolution — an LLM hallucinated chunk_id falls
    back to literal `(source)` (defensive, visible in the UI).
  - Persisting chunk_ids only on the user turn: the assistant turn is the one
    that carries citation context; storing there keeps future UI highlighting
    co-located with the answer row.
  - Single-session persistence of both turns without eng.begin(): engine.connect
    auto-commits each statement but without a transaction a crash between the
    two inserts would leave an orphan user turn. One transaction = atomic turn.
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


# Default top-k for chat retrieval. Matches synthesize's post-finalize k=6 on
# the brief-render side; keeps token budget predictable for the Sonnet call.
DEFAULT_CHAT_TOP_K: int = 6


class ChatAnswer(BaseModel):
    """Structured output from the chat synthesizer.

    Extra="forbid" so OpenAI structured-output can compile a strict JSON schema
    (matches Brief's convention).
    """

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
    """Fill the system prompt template with the investigation's subject.

    Falls back to a generic line when subject is unknown (investigation row
    missing, test fixture, etc.) — still useful but loses the off-topic
    filtering guard.
    """
    if subject:
        subject_line = f"{subject}"
    else:
        subject_line = "a company"
    return _SYSTEM_PROMPT_TEMPLATE.format(
        subject=subject or "this company",
        subject_line=subject_line,
    )


def _build_user_prompt(question: str, retrieved: list[RetrievedChunk]) -> str:
    """Format retrieved chunks + the user's question into the user-turn prompt.

    Unlike synthesize.py this does NOT use `<retrieved_content>` GUARD-01
    delimiters — chat inputs are already prompt-injection-guarded at ingest
    time (migration 0004 / Plan 03-08 injection classifier). The `[S:<id>]`
    marker is the citation hook, not a security delimiter.
    """
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
    """Call Sonnet with retrieved chunks; return structured answer.

    `subject` is the investigation's subject (company name / URL) — when
    provided, the system prompt instructs the LLM to IGNORE any chunk that's
    clearly about a different company. This is the final defense against
    corpus pollution from widen-search drift (e.g. an "is Iyin still at the
    company" question widening into Flutterwave pages).

    Fail-open on parsed=None (vs. synthesize.py which raises PipelineError) —
    a malformed chat response should degrade to a visible "I couldn't generate
    an answer" message, not 500 the whole request. The user can retype.
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
    """Replace [S:<chunk_id>] markers with markdown `([source](url))`.

    Unknown chunk_ids (defensive: LLM hallucinates an id, or retrieval returned
    a chunk later deleted) fall back to the literal `(source)` — preserves the
    visual cue that a citation was present without rendering a broken link.
    """

    def _replace(match: re.Match[str]) -> str:
        chunk_id = match.group(1).strip()
        url = url_by_chunk.get(chunk_id)
        return f"([source]({url}))" if url else "(source)"

    return _CITATION_RE.sub(_replace, answer_text)


def _load_url_by_chunk(conn, investigation_id: UUID) -> dict[str, str]:
    """Build chunk_id → url map for this investigation's sources."""
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


# ---------------------------------------------------------------------------
# Widen-search: fire a fresh Exa query when the initial retrieval is weak.
# ---------------------------------------------------------------------------
# Cosine distance threshold. Below = good match; above = weak. Tuned for
# text-embedding-3-small: in-corpus matches usually land at 0.15–0.35; 0.4+
# typically means the question is asking about something we don't have.
WEAK_RETRIEVAL_DISTANCE = 0.4


def _investigation_subject(eng: Engine, investigation_id: UUID) -> str | None:
    """Return a best-effort human subject for the investigation: the input_ref
    with the HINT_SEPARATOR suffix stripped. Used to scope widen-search
    queries so a chat turn like 'where is the headquarters' doesn't pull in
    unrelated companies' pages. Returns None if the row is missing.

    HINT_SEPARATOR is `\\n---HINT---\\n` (pipeline.py). Previously this code
    looked for `[hint:` which was the wrong separator — every subject came
    through with the hint tail still attached. Fixed by using the real
    constant via a deferred import.
    """
    from dossier.investigate.render import HINT_SEPARATOR  # noqa: PLC0415
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT input_ref FROM investigations WHERE id = :id"),
            {"id": str(investigation_id)},
        ).fetchone()
    if row is None or not row[0]:
        return None
    subject = row[0].split(HINT_SEPARATOR, 1)[0].strip()
    return subject or None


def _widen_search(
    investigation_id: UUID,
    question: str,
    eng: Engine,
) -> int:
    """Fresh Exa search for the question (subject-scoped); ingest results.

    Returns count ingested. Returns 0 and logs if Exa is unavailable /
    returns nothing / ingest errors. Never raises — chat turn must still
    complete with whatever the original retrieval produced.

    Query scoping is CRITICAL: a generic question like "where is the
    headquarters" embedded alone matches headquarters pages for any famous
    company (Microsoft, General Mills, etc.). We always prefix with the
    investigation's subject so Exa returns pages scoped to THIS company.
    Without this guard every weak chat turn poisons the corpus permanently.
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
        # No subject to scope against — refuse to widen rather than pollute
        # the corpus with unrelated companies' results.
        logger.warning("chat: widen-search skipped — no investigation subject found")
        return 0

    scoped_query = f"{subject}: {question}"
    logger.info("chat: widen-search scoped query=%r", scoped_query[:120])

    try:
        new_results = exa_tool.search(scoped_query, num_results=3)
    except Exception:
        logger.warning("chat: widen Exa call failed; continuing with existing corpus", exc_info=True)
        return 0

    if not new_results:
        return 0

    # Tag each result's metadata so future audits can identify widen-sourced
    # chunks (and a cleanup query can prune corpus pollution if the query was
    # off-target despite the subject scoping).
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
        logger.exception("chat: widen-ingest failed; continuing with existing corpus")
        return 0

    return len(tagged_results)


def run_chat_turn(
    investigation_id: UUID,
    question: str,
    *,
    engine: Engine | None = None,
    client: Any = None,
) -> tuple[str, list[str]]:
    """End-to-end: retrieve → (widen if weak) → synthesize → resolve → persist.

    Returns (assistant_content_with_resolved_citations, cited_chunk_ids).

    Widen-search: when the top-1 retrieved chunk has cosine distance above
    WEAK_RETRIEVAL_DISTANCE (or retrieval is empty), we fire a fresh Exa
    search scoped to the question itself, ingest the new chunks into this
    investigation, and retrieve again. Fail-open: if Exa errors or returns
    nothing, we answer from the original corpus (possibly with "I don't know").

    Persistence note: the user turn and the assistant turn land in one
    transaction so a mid-write crash can't leave an orphan user turn in the
    history (GET /chat would show a dangling question). Atomic turn-pair.
    """
    eng = engine if engine is not None else get_engine()

    # Resolve the subject once up-front — used by both widen-search scoping
    # and the synthesizer's subject-discipline guard.
    subject = _investigation_subject(eng, investigation_id)

    retrieved = retrieve_top_k(investigation_id, question, k=DEFAULT_CHAT_TOP_K)
    top_distance = retrieved[0].distance if retrieved else None
    is_weak = (not retrieved) or (top_distance is not None and top_distance > WEAK_RETRIEVAL_DISTANCE)

    widened_count = 0
    if is_weak:
        logger.info(
            "chat: weak retrieval (top_distance=%s); firing widen-search for question=%r",
            top_distance, question[:80],
        )
        widened_count = _widen_search(investigation_id, question, eng)
        if widened_count > 0:
            # Re-retrieve now that fresh chunks are in the corpus.
            retrieved = retrieve_top_k(investigation_id, question, k=DEFAULT_CHAT_TOP_K)

    if not retrieved:
        # Even after widen we have nothing — honest reply + persist so history reflects.
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
        eng,
        investigation_id,
        question,
        resolved,
        answer.cited_chunk_ids,
    )

    return resolved, answer.cited_chunk_ids


def _persist_turn_pair(
    eng: Engine,
    investigation_id: UUID,
    user_question: str,
    assistant_content: str,
    cited_chunk_ids: list[str],
) -> None:
    """Write user turn + assistant turn atomically in one transaction."""
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
