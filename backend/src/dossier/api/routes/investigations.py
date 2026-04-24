"""Investigations API — POST/GET/PATCH/DELETE/re-run.

Every route:
  - Is protected by Depends(require_clerk_user_id) — AUTH-01.
  - Scopes all SQL on WHERE user_id = :clerk_user_id — D-24 row scoping.
  - Uses parameterized SQL via SQLAlchemy text() — no string concat.
  - Returns structured errors via HTTPException(detail=<machine-readable string>).

POST dispatches pipeline via one of two modes, selected by DOSSIER_DISPATCH_MODE:
  - "local" (default): FastAPI BackgroundTasks + linear pipeline (D-14, Phase 2).
  - "lambda" (deployed): boto3.client('lambda').invoke(InvocationType='Event', ...)
    self-invokes this same Lambda container with {"investigation_id": ...};
    lambda_handler.py routes that to runner.handler -> run_graph (Phase 3).
Swap is confined to the dispatch helpers at the top of this file.

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

import json
import logging
import os
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.engine import Engine

from dossier.api.dependencies import require_clerk_user_id
from dossier.api.schemas import (
    ChatHistoryResponse,
    ChatMessageItem,
    ChatTurnBody,
    ChatTurnResponse,
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


def _dispatch_local(background_tasks: BackgroundTasks, investigation_id: UUID) -> None:
    """Phase 2 inline dispatch — FastAPI BackgroundTasks runs the linear pipeline.

    Used when DOSSIER_DISPATCH_MODE is unset or =local. Keeps `uv run uvicorn`
    local-dev workflow working without AWS credentials or a deployed Lambda.
    """
    background_tasks.add_task(run_investigation, investigation_id)


def _dispatch_lambda(investigation_id: UUID) -> None:
    """Phase 3 deployed dispatch — fire-and-forget self-invoke of the same Lambda.

    The container answering this request also hosts the investigate handler;
    InvocationType='Event' queues the invocation and returns immediately (the
    POST /investigations handler stays under the Function URL's ~30s budget),
    and the freshly invoked container wakes up with {"investigation_id": ...}
    which lambda_handler.py routes to runner.handler -> run_graph.

    Why boto3 + self-invoke rather than SQS/EventBridge/Step Functions:
      - SQS would need a second Lambda (SQS trigger) + a queue resource +
        IAM wiring. For 2-day demo, the Lambda quota of self-invokes is
        fine (one per POST /investigations; rate-limited to 10/user/day
        upstream via _check_rate_limit).
      - EventBridge has higher latency (best-effort seconds) and adds a rule
        resource per event pattern.
      - Step Functions is overkill when the graph itself has resume semantics
        via AsyncPostgresSaver checkpointing.

    Env vars:
      LAMBDA_FUNCTION_NAME — self-reference string injected by Terraform
        (infra/terraform/lambda.tf:locals.composed_env_vars). Distinct from
        AWS_LAMBDA_FUNCTION_NAME (AWS-injected at runtime), which runner.py
        reads for its is-Lambda security check. Do not conflate.

    boto3 is imported at call time so the unit-test suite (which never
    exercises this path; DOSSIER_DISPATCH_MODE defaults to local) doesn't
    pay the ~150ms boto3 import cost on every test run.
    """
    import boto3  # noqa: PLC0415 — intentional lazy import

    function_name = os.environ.get("LAMBDA_FUNCTION_NAME")
    if not function_name:
        raise RuntimeError(
            "LAMBDA_FUNCTION_NAME env var required when DOSSIER_DISPATCH_MODE=lambda"
        )

    client = boto3.client("lambda")  # region from AWS_REGION / IAM role default
    client.invoke(
        FunctionName=function_name,
        InvocationType="Event",  # fire-and-forget: returns immediately, ~ms latency
        Payload=json.dumps({"investigation_id": str(investigation_id)}).encode(),
    )
    logger.info(
        "dispatch_lambda: queued investigation_id=%s on function=%s",
        investigation_id,
        function_name,
    )


def _dispatch_pipeline(background_tasks: BackgroundTasks, investigation_id: UUID) -> None:
    """Mode-selecting dispatch. Reads DOSSIER_DISPATCH_MODE at call time.

    Modes:
      - "local" (default): Phase 2 BackgroundTasks + linear pipeline
      - "lambda": Phase 3 boto3 self-invoke of the investigate handler

    Read at call time (not at import time) so flipping the env var in tests or
    between local uvicorn runs takes effect without a process restart.
    """
    mode = os.environ.get("DOSSIER_DISPATCH_MODE", "local").lower()
    if mode == "lambda":
        _dispatch_lambda(investigation_id)
    elif mode == "local":
        _dispatch_local(background_tasks, investigation_id)
    else:
        raise RuntimeError(
            f"unknown DOSSIER_DISPATCH_MODE={mode!r}; expected 'local' or 'lambda'"
        )


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
# POST /investigations/upload (INPUT-03 — pitch-deck PDF, Phase 5-lite Plan 03-13)
# ---------------------------------------------------------------------------

# Demo-pressure tradeoffs:
#   - 17 MB cap: prevents large-upload DoS on the single Lambda container while
#     covering the ~98th percentile of real decks (most are 3–6 MB image-heavy;
#     occasional 15 MB outliers with embedded rasters).
#   - application/octet-stream accepted in addition to application/pdf because
#     some clients (curl without -H) send the generic MIME. Magic-byte check
#     is inside MarkItDown — it raises on non-PDF content.
#   - Dispatch is pinned to BackgroundTasks even when DOSSIER_DISPATCH_MODE=lambda
#     for name/URL investigations, because the deck's markdown isn't persisted to
#     S3. The self-invoke Lambda event would have to carry the full text as a
#     payload (256 KB Event-invoke limit would bite), or re-fetch from DB. Both
#     are out of scope; Plan 03-14 could wire it.

DECK_MAX_BYTES: int = 17 * 1024 * 1024  # 17 MB
DECK_MIN_BYTES: int = 100  # below this it's not a real PDF
DECK_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {"application/pdf", "application/octet-stream"}
)


@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CreateInvestigationResponse,
)
def upload_deck_investigation(
    background_tasks: BackgroundTasks,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
    file: Annotated[UploadFile, File(...)],
    context_hint: Annotated[str | None, Form()] = None,
) -> CreateInvestigationResponse:
    """Accept a pitch-deck PDF, convert via MarkItDown, dispatch pipeline."""
    # Deferred import — keeps the API module cheap to load; MarkItDown pulls in
    # pdfminer + lxml on first use.
    from dossier.investigate.deck import (  # noqa: PLC0415
        extract_company_from_markdown,
        pdf_to_markdown,
        run_deck_investigation,
    )

    eng = _engine()

    _check_rate_limit(eng, clerk_user_id)

    # Validate MIME + read bytes
    content_type = (file.content_type or "").lower()
    if content_type not in DECK_ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="pdf_required")

    raw = file.file.read()
    if not raw or len(raw) < DECK_MIN_BYTES:
        raise HTTPException(status_code=400, detail="empty_or_too_small")
    if len(raw) > DECK_MAX_BYTES:
        raise HTTPException(status_code=413, detail="pdf_too_large")

    filename = file.filename or "deck.pdf"

    try:
        markdown = pdf_to_markdown(raw, filename)
    except Exception as exc:  # noqa: BLE001 — surface-all conversion errors as 422
        logger.exception("MarkItDown failed on uploaded PDF: %s", filename)
        raise HTTPException(status_code=422, detail="pdf_conversion_failed") from exc

    if not markdown.strip():
        raise HTTPException(status_code=422, detail="pdf_no_text_extracted")

    _ensure_user_exists(eng, clerk_user_id)

    investigation_id = uuid4()
    # Extract subject company from the deck's cover slide via Haiku so chat
    # widen-search has a real name to scope to (filename-as-subject pulled
    # unrelated companies — see 'Iyinoluwa Aboyeji' → Flutterwave contamination).
    # Fail-open: if extraction returns None, fall back to the filename.
    extracted_company: str | None = None
    try:
        extracted_company = extract_company_from_markdown(markdown)
    except Exception:  # noqa: BLE001
        logger.exception("deck: company-name extraction threw; falling back to filename")
    display_value = extracted_company or filename
    logger.info(
        "deck upload: investigation_id=%s filename=%r extracted_company=%r using=%r",
        investigation_id, filename, extracted_company, display_value,
    )
    # input_ref is used as the investigation subject by chat widen-search and
    # the LIB-01 list card heading; also referenced by `_resolve_inputs` on the
    # deck branch. We PREFER the extracted company name but keep a filename
    # reference in the hint suffix so the library card and debug logs can
    # always link back to the upload.
    hint_parts: list[str] = []
    if extracted_company:
        hint_parts.append(f"deck: {filename}")
    if context_hint:
        hint_parts.append(context_hint)
    input_ref = _build_input_ref(display_value, " | ".join(hint_parts) or None)

    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO investigations (id, user_id, status, input_type, input_ref) "
                "VALUES (:id, :u, 'queued', 'deck', :v)"
            ),
            {"id": str(investigation_id), "u": clerk_user_id, "v": input_ref},
        )

    # Pinned to local dispatch; see the module-level note above.
    background_tasks.add_task(
        run_deck_investigation, investigation_id, markdown, filename
    )

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
# Phase 6-lite chat (Plan 03-14 / CHAT-01 / CHAT-02)
#
# GET  /investigations/:id/chat → history (ordered by created_at ASC)
# POST /investigations/:id/chat → one turn: retrieve top-k → Sonnet → persist
#
# Both routes reuse _load_user_investigation for authz (Clerk-user owns the
# investigation or 404 — D-24 row scoping). The Sonnet call happens inline in
# the POST handler (no BackgroundTasks) because the turn round-trip is the
# user-visible latency; non-streaming JSON demo-lite per 03-14-PLAN.md.
# ---------------------------------------------------------------------------

@router.get("/{investigation_id}/chat", response_model=ChatHistoryResponse)
def get_chat_history(
    investigation_id: UUID,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> ChatHistoryResponse:
    eng = _engine()
    _load_user_investigation(eng, investigation_id, clerk_user_id)  # authz check
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT role, content, cited_chunk_ids, created_at "
                "FROM chat_messages "
                "WHERE investigation_id = :id "
                "ORDER BY created_at ASC"
            ),
            {"id": str(investigation_id)},
        ).all()
    messages = [
        ChatMessageItem(
            role=r.role,
            content=r.content,
            cited_chunk_ids=r.cited_chunk_ids or [],
            created_at=r.created_at,
        )
        for r in rows
    ]
    return ChatHistoryResponse(investigation_id=investigation_id, messages=messages)


@router.post("/{investigation_id}/chat", response_model=ChatTurnResponse)
def post_chat_turn(
    investigation_id: UUID,
    body: ChatTurnBody,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> ChatTurnResponse:
    # Deferred import — keeps the routes module cheap to load and mirrors the
    # deck.pdf_to_markdown pattern (Plan 03-13). The chat module pulls openai
    # SDK + pgvector retrieval on first use.
    from dossier.investigate.chat import run_chat_turn  # noqa: PLC0415

    eng = _engine()
    row = _load_user_investigation(eng, investigation_id, clerk_user_id)

    # Require the investigation to be complete — we only chat over grounded
    # source chunks. `grounding`/`synthesizing`/`gathering` all map to the
    # running state on the UI side, which gates the chat input (see
    # frontend/components/ChatPane.tsx). Belt-and-suspenders here for direct
    # API hits.
    if row.status != "complete":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="not_ready",
        )

    answer, cited = run_chat_turn(investigation_id, body.question.strip(), engine=eng)
    return ChatTurnResponse(answer=answer, cited_chunk_ids=cited)


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
