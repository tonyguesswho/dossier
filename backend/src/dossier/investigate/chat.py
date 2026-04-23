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


_SYSTEM_PROMPT = """You are an analyst answering a VC's question about a company.

You MUST answer using ONLY the provided source chunks, which are marked
`[S:<chunk_id>]`. For every factual claim in your answer, cite at least one
chunk inline using the exact pattern `[S:<chunk_id>]`. If the chunks don't
contain information to answer the question, say so plainly — DO NOT invent
facts or cite chunks you didn't use.

Return `answer_text` (the prose answer with inline [S:xxx] markers) and
`cited_chunk_ids` (the unique chunk_ids you actually referenced)."""


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
    client: Any = None,
) -> ChatAnswer:
    """Call Sonnet with retrieved chunks; return structured answer.

    Fail-open on parsed=None (vs. synthesize.py which raises PipelineError) —
    a malformed chat response should degrade to a visible "I couldn't generate
    an answer" message, not 500 the whole request. The user can retype.
    """
    active_client = client if client is not None else strong_model()
    completion = active_client.beta.chat.completions.parse(
        model=STRONG_MODEL_ID,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
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


def run_chat_turn(
    investigation_id: UUID,
    question: str,
    *,
    engine: Engine | None = None,
    client: Any = None,
) -> tuple[str, list[str]]:
    """End-to-end: retrieve → synthesize → resolve citations → persist.

    Returns (assistant_content_with_resolved_citations, cited_chunk_ids).

    Persistence note: the user turn and the assistant turn land in one
    transaction so a mid-write crash can't leave an orphan user turn in the
    history (GET /chat would show a dangling question). Atomic turn-pair.
    """
    eng = engine if engine is not None else get_engine()

    retrieved = retrieve_top_k(investigation_id, question, k=DEFAULT_CHAT_TOP_K)
    if not retrieved:
        # Still persist the user turn + a system-ish assistant response so the
        # history reflects the attempted conversation. Without this the user
        # would see their question disappear on a no-sources investigation.
        empty_reply = "I don't have any source material for this investigation yet."
        _persist_turn_pair(eng, investigation_id, question, empty_reply, [])
        return empty_reply, []

    answer = answer_with_citations(question, retrieved, client=client)

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
