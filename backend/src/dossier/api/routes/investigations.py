"""Investigations API — POST/GET/PATCH/DELETE/re-run.

Every route:
  - Is protected by Depends(require_clerk_user_id) — AUTH-01.
  - Scopes all SQL on WHERE user_id = :clerk_user_id — D-24 row scoping.
  - Uses parameterized SQL via SQLAlchemy text() — no string concat.
  - Returns structured errors via HTTPException(detail=<machine-readable string>).

POST dispatches pipeline via FastAPI BackgroundTasks per D-14. Phase 3 swaps this
for boto3.client('lambda').invoke(InvocationType='Event', ...) — a ~2-line change
confined to `_dispatch_pipeline` below.

Rate limit (D-27 / GUARD-03):
  - 10 investigations per Clerk user per rolling 24h window.
  - Applied to POST /investigations AND POST /investigations/:id/re-run
    (both create new rows and run the pipeline).
  - Enforced via SELECT COUNT(*) — cheap; no Redis dep.

Rejected alternatives:
  - arq/Celery: requires Redis (D-14).
  - Skip rate limit: GUARD-03 requires it for v1.
  - Rate-limit in middleware: middleware runs per request; placing it inline at the
    POST handler is simpler for Phase 2 (Phase 3 can move to middleware).
  - Separate display_name column: would need migration 0003; renaming input_ref
    is semantically equivalent for Phase 2.
"""
from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.api.dependencies import require_clerk_user_id
from dossier.api.schemas import (
    CreateInvestigationBody,
    CreateInvestigationResponse,
    InvestigationBriefResponse,
    InvestigationListItem,
    InvestigationListResponse,
    InvestigationStatusResponse,
    ReRunResponse,
    RenameInvestigationBody,
    SourceListItem,
)
from dossier.core.db import get_engine
from dossier.investigate.pipeline import HINT_SEPARATOR, run_investigation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/investigations", tags=["investigations"])

RATE_LIMIT_PER_24H: int = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _engine() -> Engine:
    return get_engine()


def _strip_hint(input_ref: str) -> str:
    return input_ref.split(HINT_SEPARATOR, 1)[0]


def _build_input_ref(value: str, context_hint: str | None) -> str:
    if context_hint:
        return f"{value}{HINT_SEPARATOR}{context_hint}"
    return value


def _check_rate_limit(eng: Engine, clerk_user_id: str) -> None:
    with eng.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM investigations "
                "WHERE user_id = :u AND started_at > now() - interval '1 day'"
            ),
            {"u": clerk_user_id},
        ).scalar_one()
    if count >= RATE_LIMIT_PER_24H:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate_limited",
        )


def _ensure_user_exists(eng: Engine, clerk_user_id: str) -> None:
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id) VALUES (:u) ON CONFLICT DO NOTHING"),
            {"u": clerk_user_id},
        )


def _load_user_investigation(eng: Engine, investigation_id: UUID, clerk_user_id: str):
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, user_id, status, input_type, input_ref, started_at, "
                "       completed_at, brief_markdown, error "
                "FROM investigations WHERE id = :id AND user_id = :u"
            ),
            {"id": str(investigation_id), "u": clerk_user_id},
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


def _dispatch_pipeline(background_tasks: BackgroundTasks, investigation_id: UUID) -> None:
    """BackgroundTasks dispatch. Phase 3 swaps to boto3 lambda invoke."""
    background_tasks.add_task(run_investigation, investigation_id)


# ---------------------------------------------------------------------------
# POST /investigations (INPUT-01 / INPUT-02 / INPUT-04 / GUARD-03)
# ---------------------------------------------------------------------------

@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreateInvestigationResponse)
def create_investigation(
    body: CreateInvestigationBody,
    background_tasks: BackgroundTasks,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> CreateInvestigationResponse:
    eng = _engine()

    _check_rate_limit(eng, clerk_user_id)

    # Run URL validation (outside of Pydantic so we can surface guardrail_rejected)
    try:
        normalized = body.normalized_value()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="guardrail_rejected",
        ) from exc

    _ensure_user_exists(eng, clerk_user_id)

    investigation_id = uuid4()
    input_ref = _build_input_ref(normalized, body.context_hint)

    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO investigations (id, user_id, status, input_type, input_ref)
                VALUES (:id, :u, 'queued', :k, :v)
                """
            ),
            {"id": str(investigation_id), "u": clerk_user_id, "k": body.kind, "v": input_ref},
        )

    _dispatch_pipeline(background_tasks, investigation_id)

    return CreateInvestigationResponse(id=investigation_id, status="queued")


# ---------------------------------------------------------------------------
# GET /investigations (LIB-01)
# ---------------------------------------------------------------------------

@router.get("", response_model=InvestigationListResponse)
def list_investigations(
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> InvestigationListResponse:
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, input_ref, status, started_at "
                "FROM investigations WHERE user_id = :u "
                "ORDER BY started_at DESC"
            ),
            {"u": clerk_user_id},
        ).fetchall()
    items = [
        InvestigationListItem(
            id=r.id,
            display_name=_strip_hint(r.input_ref or ""),
            status=r.status,
            started_at=r.started_at,
        )
        for r in rows
    ]
    return InvestigationListResponse(items=items)


# ---------------------------------------------------------------------------
# GET /investigations/:id/status (INVEST-03)
# ---------------------------------------------------------------------------

@router.get("/{investigation_id}/status", response_model=InvestigationStatusResponse)
def get_status(
    investigation_id: UUID,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> InvestigationStatusResponse:
    eng = _engine()
    row = _load_user_investigation(eng, investigation_id, clerk_user_id)

    with eng.connect() as conn:
        sources_count = conn.execute(
            text("SELECT COUNT(*) FROM sources WHERE investigation_id = :id"),
            {"id": str(investigation_id)},
        ).scalar_one()
        claims_count = conn.execute(
            text("SELECT COUNT(*) FROM claims WHERE investigation_id = :id"),
            {"id": str(investigation_id)},
        ).scalar_one()

    return InvestigationStatusResponse(
        id=row.id,
        status=row.status,
        display_name=_strip_hint(row.input_ref or ""),
        sources_count=sources_count,
        claims_count=claims_count,
        error=row.error,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


# ---------------------------------------------------------------------------
# GET /investigations/:id/brief (BRIEF-01 / BRIEF-06 — markdown payload)
# ---------------------------------------------------------------------------

@router.get("/{investigation_id}/brief", response_model=InvestigationBriefResponse)
def get_brief(
    investigation_id: UUID,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> InvestigationBriefResponse:
    eng = _engine()
    row = _load_user_investigation(eng, investigation_id, clerk_user_id)
    if row.status not in ("complete", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": "not_ready", "status": row.status},
        )

    with eng.connect() as conn:
        sources = conn.execute(
            text(
                "SELECT id, url, source_kind FROM sources "
                "WHERE investigation_id = :id ORDER BY fetched_at ASC"
            ),
            {"id": str(investigation_id)},
        ).fetchall()

    return InvestigationBriefResponse(
        id=row.id,
        display_name=_strip_hint(row.input_ref or ""),
        status=row.status,
        brief_markdown=row.brief_markdown or "",
        sources=[SourceListItem(id=s.id, url=s.url, source_kind=s.source_kind) for s in sources],
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


# ---------------------------------------------------------------------------
# PATCH /investigations/:id (LIB-03 rename)
# ---------------------------------------------------------------------------

@router.patch("/{investigation_id}", response_model=InvestigationListItem)
def rename_investigation(
    investigation_id: UUID,
    body: RenameInvestigationBody,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> InvestigationListItem:
    eng = _engine()
    row = _load_user_investigation(eng, investigation_id, clerk_user_id)

    # Preserve the HINT_SEPARATOR tail if present (so the context_hint stays attached).
    original = row.input_ref or ""
    _, sep, hint = original.partition(HINT_SEPARATOR)
    new_input_ref = body.display_name + (HINT_SEPARATOR + hint if sep else "")

    with eng.begin() as conn:
        conn.execute(
            text("UPDATE investigations SET input_ref = :v WHERE id = :id AND user_id = :u"),
            {"v": new_input_ref, "id": str(investigation_id), "u": clerk_user_id},
        )

    return InvestigationListItem(
        id=row.id,
        display_name=body.display_name,
        status=row.status,
        started_at=row.started_at,
    )


# ---------------------------------------------------------------------------
# DELETE /investigations/:id (LIB-03 + D-19 hard delete)
# ---------------------------------------------------------------------------

@router.delete("/{investigation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_investigation(
    investigation_id: UUID,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> None:
    eng = _engine()
    _load_user_investigation(eng, investigation_id, clerk_user_id)  # 404 if not owner

    # ON DELETE CASCADE on sources → source_chunks; investigations also cascades to claims
    # per migration 0001. One DELETE wipes the dependent rows.
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM investigations WHERE id = :id AND user_id = :u"),
            {"id": str(investigation_id), "u": clerk_user_id},
        )


# ---------------------------------------------------------------------------
# POST /investigations/:id/re-run (LIB-02 + D-18)
# ---------------------------------------------------------------------------

@router.post("/{investigation_id}/re-run", status_code=status.HTTP_202_ACCEPTED, response_model=ReRunResponse)
def re_run_investigation(
    investigation_id: UUID,
    background_tasks: BackgroundTasks,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> ReRunResponse:
    eng = _engine()
    original = _load_user_investigation(eng, investigation_id, clerk_user_id)

    _check_rate_limit(eng, clerk_user_id)

    new_id = uuid4()
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO investigations (id, user_id, status, input_type, input_ref, re_run_of)
                VALUES (:id, :u, 'queued', :k, :v, :orig)
                """
            ),
            {
                "id": str(new_id),
                "u": clerk_user_id,
                "k": original.input_type,
                "v": original.input_ref,
                "orig": str(investigation_id),
            },
        )

    _dispatch_pipeline(background_tasks, new_id)
    return ReRunResponse(id=new_id, status="queued")


__all__ = ["router"]
