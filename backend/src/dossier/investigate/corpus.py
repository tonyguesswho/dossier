from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.engine import Engine

from dossier.investigate import repository as repo
from dossier.investigate.ingest import ingest_tool_results
from dossier.investigate.retrieve import (
    DEFAULT_TOP_K,
    RetrievedChunk,
    retrieve_top_k,
)
from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)


WIDEN_NUM_RESULTS: int = 3


def top_k(
    investigation_id: UUID,
    query: str,
    *,
    k: int = DEFAULT_TOP_K,
    engine: Engine | None = None,
) -> list[RetrievedChunk]:
    return retrieve_top_k(investigation_id, query, k=k, engine=engine)


def urls_by_chunk(eng: Engine, investigation_id: UUID) -> dict[str, str]:
    return repo.url_by_chunk(eng, investigation_id)


def widen(
    investigation_id: UUID,
    *,
    subject: str | None,
    question: str,
    engine: Engine,
) -> int:
    # Subject scoping is critical: a generic question alone matches headquarters
    # pages for any famous company and permanently pollutes the corpus.
    # Never raises — chat must complete its turn with whatever is already in the corpus.
    if not subject:
        logger.warning("corpus.widen: skipped — no subject provided")
        return 0

    try:
        from dossier.investigate.tools import exa as exa_tool  # noqa: PLC0415
    except Exception:
        logger.exception("corpus.widen: exa import failed")
        return 0

    scoped_query = f"{subject}: {question}"
    logger.info("corpus.widen: query=%r", scoped_query[:120])

    try:
        new_results = exa_tool.search(scoped_query, num_results=WIDEN_NUM_RESULTS)
    except Exception:
        logger.warning("corpus.widen: Exa failed", exc_info=True)
        return 0

    if not new_results:
        return 0

    # Tag widen metadata so an audit can prune corpus pollution if the question went off-target.
    tagged = [
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
        ingest_tool_results(investigation_id, tagged, engine=engine)
    except Exception:
        logger.exception("corpus.widen: ingest failed")
        return 0

    return len(tagged)

