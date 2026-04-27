from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Engine

from dossier.core.db import get_engine
from dossier.core.llm import structured_call
from dossier.investigate import corpus, repository as repo
from dossier.investigate.retrieve import RetrievedChunk

logger = logging.getLogger(__name__)


DEFAULT_CHAT_TOP_K: int = 6


class ChatAnswer(BaseModel):
    # extra='forbid' so OpenAI structured-output compiles a strict schema.
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
    # Fail-open on parsed=None — a malformed chat response degrades to a visible apology, never 500s.
    parsed = structured_call(
        ChatAnswer,
        messages=[
            {"role": "system", "content": _build_system_prompt(subject)},
            {"role": "user", "content": _build_user_prompt(question, retrieved)},
        ],
        client=client,
    )
    if parsed is None:
        logger.warning("chat: parsed=None; returning empty answer")
        return ChatAnswer(
            answer_text="I couldn't generate an answer.",
            cited_chunk_ids=[],
        )
    return parsed


_CITATION_RE = re.compile(r"\[S:([^\]]+)\]")


def resolve_citations(answer_text: str, url_by_chunk: dict[str, str]) -> str:
    # Unknown ids render as `(source)` (no link) so a hallucinated id doesn't break navigation.
    def _replace(match: re.Match[str]) -> str:
        chunk_id = match.group(1).strip()
        url = url_by_chunk.get(chunk_id)
        return f"([source]({url}))" if url else "(source)"

    return _CITATION_RE.sub(_replace, answer_text)


# Tuned for text-embedding-3-small: in-corpus matches usually land at
# 0.15–0.35; 0.4+ typically means we don't have what the user asked about.
WEAK_RETRIEVAL_DISTANCE = 0.4


def run_chat_turn(
    investigation_id: UUID,
    question: str,
    *,
    engine: Engine | None = None,
    client: Any = None,
) -> tuple[str, list[str]]:
    eng = engine if engine is not None else get_engine()

    subject = repo.get_investigation_subject(eng, investigation_id)

    retrieved = corpus.top_k(investigation_id, question, k=DEFAULT_CHAT_TOP_K)
    top_distance = retrieved[0].distance if retrieved else None
    is_weak = (not retrieved) or (top_distance is not None and top_distance > WEAK_RETRIEVAL_DISTANCE)

    if is_weak:
        logger.info(
            "chat: weak retrieval (top_distance=%s) — widening for %r",
            top_distance, question[:80],
        )
        widened_count = corpus.widen(
            investigation_id, subject=subject, question=question, engine=eng
        )
        if widened_count > 0:
            retrieved = corpus.top_k(investigation_id, question, k=DEFAULT_CHAT_TOP_K)

    if not retrieved:
        empty_reply = "I don't have any source material for this investigation yet."
        repo.insert_chat_turn_pair(
            eng, investigation_id,
            user_question=question,
            assistant_content=empty_reply,
            cited_chunk_ids=[],
        )
        return empty_reply, []

    answer = answer_with_citations(
        question, retrieved, subject=subject, client=client
    )

    url_by_chunk = corpus.urls_by_chunk(eng, investigation_id)
    resolved = resolve_citations(answer.answer_text, url_by_chunk)

    repo.insert_chat_turn_pair(
        eng, investigation_id,
        user_question=question,
        assistant_content=resolved,
        cited_chunk_ids=answer.cited_chunk_ids,
    )

    return resolved, answer.cited_chunk_ids


__all__ = [
    "ChatAnswer",
    "DEFAULT_CHAT_TOP_K",
    "answer_with_citations",
    "resolve_citations",
    "run_chat_turn",
]
