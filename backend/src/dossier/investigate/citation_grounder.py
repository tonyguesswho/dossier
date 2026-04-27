from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy.engine import Engine

from dossier.investigate.brief_schema import Brief
from dossier.investigate.ground import GroundStats, ground_claims
from dossier.investigate.render import brief_to_markdown
from dossier.investigate.retrieve import retrieve_top_k

logger = logging.getLogger(__name__)


async def ground_and_render(
    investigation_id: UUID,
    company: str,
    brief: Brief,
    *,
    engine: Engine | None = None,
) -> tuple[str, GroundStats]:
    """Retrieve chunks, ground all claims, and render to markdown.

    Single seam for the citation pipeline. Callers get (brief_markdown, stats)
    without coordinating retrieve → ground → url_by_chunk → render internally.
    """
    from dossier.core.db import get_async_session
    from dossier.investigate import repository as repo

    retrieved = await asyncio.to_thread(
        retrieve_top_k, investigation_id, company, k=50, engine=engine
    )

    stats = await asyncio.to_thread(
        ground_claims, investigation_id, brief, retrieved, engine=engine
    )

    # url_by_chunk spans the whole corpus — grounder may pin outside the top-k window.
    url_by_chunk: dict[str, str] = {}
    try:
        async with get_async_session() as session:
            url_by_chunk = await repo.aurl_by_chunk(session, str(investigation_id))
    except Exception:  # noqa: BLE001 — render must not block completion
        logger.exception(
            "citation_grounder: url_by_chunk lookup failed for %s — rendering without links",
            investigation_id,
        )

    brief_md = brief_to_markdown(brief, url_by_chunk)
    logger.info(
        "citation_grounder: %s — %d/%d claims grounded (precision=%.2f)",
        investigation_id,
        stats.claims_grounded,
        stats.claims_written,
        stats.precision,
    )
    return brief_md, stats
