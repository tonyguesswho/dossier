"""Investigations API — POST/GET/PATCH/DELETE/re-run.

Every route requires Depends(require_clerk_user_id), scopes SQL on
`user_id = :clerk_user_id`, and uses parameterized text() (no string concat).

POST dispatches the pipeline via DOSSIER_DISPATCH_MODE:
  - "local" (default): FastAPI BackgroundTasks runs the graph in-process.
  - "lambda": boto3.invoke(InvocationType='Event') self-invokes this same
    container; lambda_handler routes the event to runner.run_graph.

Rate limit: 10 investigations per Clerk user per rolling 24h, enforced by
SELECT COUNT(*) — cheap, no Redis.
"""
from __future__ import annotations

import json
import logging
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
    ScorecardResponse,
    RenameInvestigationBody,
    SourceListItem,
)
from dossier.core.db import get_engine
from dossier.core.settings import get_settings
from dossier.investigate.render import HINT_SEPARATOR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/investigations", tags=["investigations"])

RATE_LIMIT_PER_24H: int = 10


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


def _run_graph_sync(investigation_id: UUID) -> None:
    """BackgroundTasks takes sync callables; runner.run_graph is async."""
    import asyncio  # noqa: PLC0415
    from dossier.investigate.graph.runner import run_graph  # noqa: PLC0415
    asyncio.run(run_graph(str(investigation_id)))


def _dispatch_local(background_tasks: BackgroundTasks, investigation_id: UUID) -> None:
    background_tasks.add_task(_run_graph_sync, investigation_id)


def _dispatch_lambda(investigation_id: UUID) -> None:
    """Fire-and-forget self-invoke. The same container hosts the investigate
    handler; InvocationType='Event' returns immediately so POST stays under the
    Function URL's ~30s budget.

    Picked over SQS / EventBridge / Step Functions because the per-user 10/day
    rate limit caps Lambda self-invocations well below quota and avoids the
    extra resources / IAM wiring those alternatives need.
    """
    import boto3  # noqa: PLC0415

    function_name = get_settings().lambda_function_name
    if not function_name:
        raise RuntimeError(
            "LAMBDA_FUNCTION_NAME env var required when DOSSIER_DISPATCH_MODE=lambda"
        )

    client = boto3.client("lambda")
    client.invoke(
        FunctionName=function_name,
        InvocationType="Event",
        Payload=json.dumps({"investigation_id": str(investigation_id)}).encode(),
    )
    logger.info(
        "dispatch_lambda: queued investigation_id=%s on function=%s",
        investigation_id, function_name,
    )


def _dispatch_pipeline(background_tasks: BackgroundTasks, investigation_id: UUID) -> None:
    """Read DOSSIER_DISPATCH_MODE at call time so flipping it doesn't need a
    process restart.
    """
    mode = get_settings().dispatch_mode
    if mode == "lambda":
        _dispatch_lambda(investigation_id)
    elif mode == "local":
        _dispatch_local(background_tasks, investigation_id)
    else:
        raise RuntimeError(f"unknown DOSSIER_DISPATCH_MODE={mode!r}")


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CreateInvestigationResponse)
def create_investigation(
    body: CreateInvestigationBody,
    background_tasks: BackgroundTasks,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> CreateInvestigationResponse:
    eng = _engine()

    _check_rate_limit(eng, clerk_user_id)

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


# 17 MB cap covers the ~98th percentile of real decks; small enough to not
# DoS the single Lambda. application/octet-stream is accepted because curl
# without -H sends that — magic-byte check is inside MarkItDown.
DECK_MAX_BYTES: int = 17 * 1024 * 1024
DECK_MIN_BYTES: int = 100
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
    """Convert an uploaded PDF via MarkItDown, dispatch as a deck investigation."""
    from dossier.investigate.deck import (  # noqa: PLC0415
        extract_company_from_markdown,
        pdf_to_markdown,
        run_deck_investigation,
    )

    eng = _engine()

    _check_rate_limit(eng, clerk_user_id)

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
    except Exception as exc:  # noqa: BLE001
        logger.exception("MarkItDown failed on uploaded PDF: %s", filename)
        raise HTTPException(status_code=422, detail="pdf_conversion_failed") from exc

    if not markdown.strip():
        raise HTTPException(status_code=422, detail="pdf_no_text_extracted")

    _ensure_user_exists(eng, clerk_user_id)

    investigation_id = uuid4()
    # Extract subject company from the cover slide via Haiku — using the
    # filename pulled in unrelated companies during chat widen-search.
    extracted_company: str | None = None
    try:
        extracted_company = extract_company_from_markdown(markdown)
    except Exception:  # noqa: BLE001
        logger.exception("deck: company extraction failed; falling back to filename")
    display_value = extracted_company or filename
    logger.info(
        "deck upload: investigation_id=%s filename=%r extracted=%r using=%r",
        investigation_id, filename, extracted_company, display_value,
    )
    # Keep the filename in the hint suffix so the library card and logs can
    # always link back to the upload, even when display_value is the company.
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

    # Pinned to local dispatch — deck markdown isn't persisted to S3 and the
    # 256 KB Event-invoke payload limit can't carry it.
    background_tasks.add_task(
        run_deck_investigation, investigation_id, markdown, filename
    )

    return CreateInvestigationResponse(id=investigation_id, status="queued")


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

    scorecard = _compute_scorecard(eng, investigation_id) if row.status == "complete" else None

    return InvestigationBriefResponse(
        id=row.id,
        display_name=_strip_hint(row.input_ref or ""),
        status=row.status,
        brief_markdown=row.brief_markdown or "",
        sources=[SourceListItem(id=s.id, url=s.url, source_kind=s.source_kind) for s in sources],
        started_at=row.started_at,
        completed_at=row.completed_at,
        scorecard=scorecard,
    )


def _compute_scorecard(eng: Engine, investigation_id: UUID) -> ScorecardResponse | None:
    """Citation precision + grounding rate for one investigation.

    grounded_span_start/end are SOURCE-absolute; chunk.text is local — translate
    via chunk_char_start when checking the substring match.
    """
    from dossier.eval.scorer import normalize  # noqa: PLC0415
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.claim_text, c.grounded_source_chunk_id::text AS chunk_id, "
                "       c.grounded_span_start, c.grounded_span_end, "
                "       sc.text AS chunk_text, sc.char_start AS chunk_char_start "
                "FROM claims c "
                "LEFT JOIN source_chunks sc ON c.grounded_source_chunk_id = sc.id "
                "WHERE c.investigation_id = :iid"
            ),
            {"iid": str(investigation_id)},
        ).all()
    if not rows:
        return None
    total = len(rows)
    grounded = 0
    hits = 0
    for r in rows:
        if r.chunk_id and r.chunk_text is not None and r.grounded_span_start is not None:
            grounded += 1
            local_start = r.grounded_span_start - (r.chunk_char_start or 0)
            local_end = r.grounded_span_end - (r.chunk_char_start or 0)
            quoted = r.chunk_text[local_start:local_end]
            if quoted:
                nq = normalize(quoted)
                nc = normalize(r.chunk_text)
                if nq and nq in nc:
                    hits += 1
    precision = (hits / grounded) if grounded else 0.0
    grounding_rate = (grounded / total) if total else 0.0
    return ScorecardResponse(
        citation_precision=precision,
        grounding_rate=grounding_rate,
        total_claims=total,
        grounded_claims=grounded,
    )


@router.get("/{investigation_id}/chat", response_model=ChatHistoryResponse)
def get_chat_history(
    investigation_id: UUID,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> ChatHistoryResponse:
    eng = _engine()
    _load_user_investigation(eng, investigation_id, clerk_user_id)
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
    from dossier.investigate.chat import run_chat_turn  # noqa: PLC0415

    eng = _engine()
    row = _load_user_investigation(eng, investigation_id, clerk_user_id)

    if row.status != "complete":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="not_ready",
        )

    answer, cited = run_chat_turn(investigation_id, body.question.strip(), engine=eng)
    return ChatTurnResponse(answer=answer, cited_chunk_ids=cited)


@router.patch("/{investigation_id}", response_model=InvestigationListItem)
def rename_investigation(
    investigation_id: UUID,
    body: RenameInvestigationBody,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> InvestigationListItem:
    eng = _engine()
    row = _load_user_investigation(eng, investigation_id, clerk_user_id)

    # Preserve the HINT_SEPARATOR tail so context_hint stays attached.
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


@router.delete("/{investigation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_investigation(
    investigation_id: UUID,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> None:
    eng = _engine()
    _load_user_investigation(eng, investigation_id, clerk_user_id)

    # ON DELETE CASCADE on sources → source_chunks; investigations → claims.
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM investigations WHERE id = :id AND user_id = :u"),
            {"id": str(investigation_id), "u": clerk_user_id},
        )


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
