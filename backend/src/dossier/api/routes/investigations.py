from __future__ import annotations

import json
import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
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
from dossier.investigate import repository as repo
from dossier.investigate.input_ref import InvestigationInput
from dossier.investigate.scorecard import compute_scorecard

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/investigations", tags=["investigations"])

RATE_LIMIT_PER_24H: int = 10


def _engine() -> Engine:
    return get_engine()


def _check_rate_limit(eng: Engine, clerk_user_id: str) -> None:
    if repo.count_recent_24h(eng, clerk_user_id) >= RATE_LIMIT_PER_24H:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate_limited",
        )


def _load_user_investigation(eng: Engine, investigation_id: UUID, clerk_user_id: str):
    row = repo.get_investigation_for_user(eng, investigation_id, clerk_user_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")
    return row


def _dispatch_pipeline(background_tasks: BackgroundTasks, investigation_id: UUID) -> None:
    # Read mode at call time so flipping the env var doesn't need a process restart.
    mode = get_settings().dispatch_mode
    if mode == "local":
        from dossier.investigate.graph.runner import run_graph_sync  # noqa: PLC0415
        background_tasks.add_task(run_graph_sync, str(investigation_id))
    elif mode == "lambda":
        # Fire-and-forget self-invoke; Event returns immediately so POST stays under the 30s URL budget.
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

    repo.upsert_user(eng, clerk_user_id)

    investigation_id = uuid4()
    input_ref = InvestigationInput(value=normalized, context_hint=body.context_hint).serialize()

    repo.insert_investigation(
        eng,
        investigation_id=investigation_id,
        user_id=clerk_user_id,
        kind=body.kind,
        input_ref=input_ref,
    )

    _dispatch_pipeline(background_tasks, investigation_id)

    return CreateInvestigationResponse(id=investigation_id, status="queued")


# 17 MB ≈ 98th percentile of real decks. octet-stream is accepted because curl without -H sends it.
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

    repo.upsert_user(eng, clerk_user_id)

    investigation_id = uuid4()
    # Use Haiku to extract subject — using filename pulled in unrelated companies during widen-search.
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
    hint_parts: list[str] = []
    if extracted_company:
        hint_parts.append(f"deck: {filename}")
    if context_hint:
        hint_parts.append(context_hint)
    input_ref = InvestigationInput(
        value=display_value, context_hint=" | ".join(hint_parts) or None
    ).serialize()

    repo.insert_investigation(
        eng,
        investigation_id=investigation_id,
        user_id=clerk_user_id,
        kind="deck",
        input_ref=input_ref,
    )

    # Local-only: deck markdown can't fit the 256 KB Event-invoke payload + isn't on S3 yet.
    background_tasks.add_task(
        run_deck_investigation, investigation_id, markdown, filename
    )

    return CreateInvestigationResponse(id=investigation_id, status="queued")


@router.get("", response_model=InvestigationListResponse)
def list_investigations(
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> InvestigationListResponse:
    eng = _engine()
    rows = repo.list_investigations_for_user(eng, clerk_user_id)
    items = [
        InvestigationListItem(
            id=r.id,
            display_name=InvestigationInput.parse(r.input_ref).value,
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

    sources_count = repo.count_sources(eng, investigation_id)
    claims_count = repo.count_claims(eng, investigation_id)

    return InvestigationStatusResponse(
        id=row.id,
        status=row.status,
        display_name=InvestigationInput.parse(row.input_ref).value,
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

    sources = repo.list_sources(eng, investigation_id)

    scorecard: ScorecardResponse | None = None
    if row.status == "complete":
        rows = repo.claim_grounding_rows(eng, investigation_id)
        scorecard_data = compute_scorecard(rows)
        if scorecard_data is not None:
            scorecard = ScorecardResponse(**scorecard_data)

    return InvestigationBriefResponse(
        id=row.id,
        display_name=InvestigationInput.parse(row.input_ref).value,
        status=row.status,
        brief_markdown=row.brief_markdown or "",
        sources=[SourceListItem(id=s.id, url=s.url, source_kind=s.source_kind) for s in sources],
        started_at=row.started_at,
        completed_at=row.completed_at,
        scorecard=scorecard,
    )


@router.get("/{investigation_id}/chat", response_model=ChatHistoryResponse)
def get_chat_history(
    investigation_id: UUID,
    clerk_user_id: Annotated[str, Depends(require_clerk_user_id)],
) -> ChatHistoryResponse:
    eng = _engine()
    _load_user_investigation(eng, investigation_id, clerk_user_id)
    rows = repo.list_chat_messages(eng, investigation_id)
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

    new_input_ref = (
        InvestigationInput.parse(row.input_ref)
        .with_value(body.display_name)
        .serialize()
    )

    repo.update_input_ref(eng, investigation_id, clerk_user_id, new_input_ref)

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
    repo.delete_investigation_for_user(eng, investigation_id, clerk_user_id)


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
    repo.insert_investigation(
        eng,
        investigation_id=new_id,
        user_id=clerk_user_id,
        kind=original.input_type,
        input_ref=original.input_ref,
        re_run_of=investigation_id,
    )

    _dispatch_pipeline(background_tasks, new_id)
    return ReRunResponse(id=new_id, status="queued")


__all__ = ["router"]
