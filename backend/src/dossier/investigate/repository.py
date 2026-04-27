from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession


# Auth scoping (`AND user_id = :u`) is enforced inside this module so the
# `_for_user` suffix is the discipline that keeps cross-tenant leaks out.


def upsert_user(eng: Engine, user_id: str) -> None:
    with eng.begin() as conn:
        conn.execute(
            text("INSERT INTO users (id) VALUES (:u) ON CONFLICT DO NOTHING"),
            {"u": user_id},
        )


def count_recent_24h(eng: Engine, user_id: str) -> int:
    with eng.connect() as conn:
        return conn.execute(
            text(
                "SELECT COUNT(*) FROM investigations "
                "WHERE user_id = :u AND started_at > now() - interval '1 day'"
            ),
            {"u": user_id},
        ).scalar_one()


def insert_investigation(
    eng: Engine,
    *,
    investigation_id: UUID,
    user_id: str,
    kind: str,
    input_ref: str,
    re_run_of: UUID | None = None,
) -> None:
    if re_run_of is None:
        sql = (
            "INSERT INTO investigations (id, user_id, status, input_type, input_ref) "
            "VALUES (:id, :u, 'queued', :k, :v)"
        )
        params: dict[str, Any] = {
            "id": str(investigation_id),
            "u": user_id,
            "k": kind,
            "v": input_ref,
        }
    else:
        sql = (
            "INSERT INTO investigations "
            "(id, user_id, status, input_type, input_ref, re_run_of) "
            "VALUES (:id, :u, 'queued', :k, :v, :orig)"
        )
        params = {
            "id": str(investigation_id),
            "u": user_id,
            "k": kind,
            "v": input_ref,
            "orig": str(re_run_of),
        }
    with eng.begin() as conn:
        conn.execute(text(sql), params)


def get_investigation_for_user(
    eng: Engine, investigation_id: UUID, user_id: str
):
    with eng.connect() as conn:
        return conn.execute(
            text(
                "SELECT id, user_id, status, input_type, input_ref, started_at, "
                "       completed_at, brief_markdown, error "
                "FROM investigations WHERE id = :id AND user_id = :u"
            ),
            {"id": str(investigation_id), "u": user_id},
        ).fetchone()


def list_investigations_for_user(eng: Engine, user_id: str) -> list:
    with eng.connect() as conn:
        return conn.execute(
            text(
                "SELECT id, input_ref, status, started_at "
                "FROM investigations WHERE user_id = :u "
                "ORDER BY started_at DESC"
            ),
            {"u": user_id},
        ).fetchall()


def count_sources(eng: Engine, investigation_id: UUID) -> int:
    with eng.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM sources WHERE investigation_id = :id"),
            {"id": str(investigation_id)},
        ).scalar_one()


def count_claims(eng: Engine, investigation_id: UUID) -> int:
    with eng.connect() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM claims WHERE investigation_id = :id"),
            {"id": str(investigation_id)},
        ).scalar_one()


def list_sources(eng: Engine, investigation_id: UUID) -> list:
    with eng.connect() as conn:
        return conn.execute(
            text(
                "SELECT id, url, source_kind FROM sources "
                "WHERE investigation_id = :id ORDER BY fetched_at ASC"
            ),
            {"id": str(investigation_id)},
        ).fetchall()


def update_input_ref(
    eng: Engine, investigation_id: UUID, user_id: str, new_input_ref: str
) -> None:
    with eng.begin() as conn:
        conn.execute(
            text(
                "UPDATE investigations SET input_ref = :v "
                "WHERE id = :id AND user_id = :u"
            ),
            {"v": new_input_ref, "id": str(investigation_id), "u": user_id},
        )


def delete_investigation_for_user(
    eng: Engine, investigation_id: UUID, user_id: str
) -> None:
    # Cascades via FK to sources -> source_chunks -> claims.
    with eng.begin() as conn:
        conn.execute(
            text("DELETE FROM investigations WHERE id = :id AND user_id = :u"),
            {"id": str(investigation_id), "u": user_id},
        )


def list_chat_messages(eng: Engine, investigation_id: UUID) -> list:
    with eng.connect() as conn:
        return conn.execute(
            text(
                "SELECT role, content, cited_chunk_ids, created_at "
                "FROM chat_messages WHERE investigation_id = :id "
                "ORDER BY created_at ASC"
            ),
            {"id": str(investigation_id)},
        ).all()


def insert_chat_turn_pair(
    eng: Engine,
    investigation_id: UUID,
    *,
    user_question: str,
    assistant_content: str,
    cited_chunk_ids: list[str],
) -> None:
    # Both messages in one transaction so an interrupted write can't leave a dangling user turn.
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


def get_investigation_subject(eng: Engine, investigation_id: UUID) -> str | None:
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


def url_by_chunk(eng: Engine, investigation_id: UUID) -> dict[str, str]:
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT sc.id::text AS chunk_id, s.url AS url "
                "FROM source_chunks sc "
                "JOIN sources s ON sc.source_id = s.id "
                "WHERE s.investigation_id = :inv_id"
            ),
            {"inv_id": str(investigation_id)},
        ).all()
    return {r.chunk_id: r.url for r in rows}


def claim_grounding_rows(eng: Engine, investigation_id: UUID) -> list:
    # grounded_span_start/end are SOURCE-absolute; chunk_text is chunk-local. Translate via chunk_char_start.
    with eng.connect() as conn:
        return conn.execute(
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


def mark_failed_with_error(
    eng: Engine, investigation_id: UUID, error: str
) -> None:
    with eng.begin() as conn:
        conn.execute(
            text(
                "UPDATE investigations "
                "SET status = CAST('failed' AS investigation_status), "
                "    error = :e, "
                "    completed_at = now() "
                "WHERE id = :i"
            ),
            {"e": error, "i": str(investigation_id)},
        )


async def aget_investigation_metadata(session: AsyncSession, investigation_id: str):
    result = await session.execute(
        text(
            "SELECT input_type, input_ref, langfuse_trace_id "
            "FROM investigations WHERE id = :id"
        ),
        {"id": investigation_id},
    )
    return result.fetchone()


async def aupdate_status(
    session: AsyncSession, investigation_id: str, status: str
) -> None:
    await session.execute(
        text(
            "UPDATE investigations "
            "SET status = CAST(:s AS investigation_status) "
            "WHERE id = CAST(:id AS UUID)"
        ),
        {"s": status, "id": investigation_id},
    )


async def acomplete_investigation(
    session: AsyncSession, investigation_id: str, brief_markdown: str
) -> None:
    # Caller batches with the grounded-claim SELECT in one transaction (see finalize.run).
    await session.execute(
        text(
            "UPDATE investigations "
            "SET status = CAST(:s AS investigation_status), "
            "    completed_at = now(), "
            "    brief_markdown = :md "
            "WHERE id = CAST(:id AS UUID)"
        ),
        {"s": "complete", "md": brief_markdown, "id": investigation_id},
    )


async def alist_grounded_claims(session: AsyncSession, investigation_id: str):
    return await session.execute(
        text(
            "SELECT id, section, claim_text, "
            "       grounded_source_chunk_id, grounded_span_start, grounded_span_end "
            "FROM claims "
            "WHERE investigation_id = CAST(:iid AS UUID) "
            "  AND grounded_source_chunk_id IS NOT NULL "
            "ORDER BY section, ordinal"
        ),
        {"iid": investigation_id},
    )


async def aurl_by_chunk(
    session: AsyncSession, investigation_id: str
) -> dict[str, str]:
    result = await session.execute(
        text(
            "SELECT sc.id::text AS chunk_id, s.url AS url "
            "FROM source_chunks sc "
            "JOIN sources s ON sc.source_id = s.id "
            "WHERE s.investigation_id = CAST(:iid AS UUID)"
        ),
        {"iid": investigation_id},
    )
    if result is None:
        return {}
    return {r.chunk_id: r.url for r in result}


async def ainsert_injection_attempt(
    session: AsyncSession,
    investigation_id: str,
    *,
    url: str,
    payload: str,
    reason: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO injection_attempts "
            "(investigation_id, url, raw_payload, verdict, reason) "
            "VALUES (CAST(:inv AS UUID), :url, :payload, :verdict, :reason)"
        ),
        {
            "inv": investigation_id,
            "url": url,
            "payload": payload,
            "verdict": "injection",
            "reason": reason,
        },
    )


__all__ = [
    "acomplete_investigation",
    "aget_investigation_metadata",
    "ainsert_injection_attempt",
    "alist_grounded_claims",
    "aupdate_status",
    "aurl_by_chunk",
    "claim_grounding_rows",
    "count_claims",
    "count_recent_24h",
    "count_sources",
    "delete_investigation_for_user",
    "get_investigation_for_user",
    "get_investigation_subject",
    "insert_chat_turn_pair",
    "insert_investigation",
    "list_chat_messages",
    "list_investigations_for_user",
    "list_sources",
    "mark_failed_with_error",
    "update_input_ref",
    "upsert_user",
    "url_by_chunk",
]
