"""Add chat_messages table for Phase 6-lite basic RAG chat (Plan 03-14).

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-23

Schema source: .planning/phases/03-langgraph-agent-aws-deployment/03-14-PLAN.md.

One row per chat turn (user or assistant). A turn-pair (user question +
assistant answer) is written atomically in dossier.investigate.chat.run_chat_turn().
Assistant rows carry the list of cited chunk_ids as JSONB (a denormalised mirror
of the inline `[S:<chunk_id>]` markers already embedded in content) for future
analytics or UI highlighting; v1 UI renders content directly as markdown.

Rejected alternatives:
  - Separate chat_threads table keyed by investigation_id: Phase 6-lite scope is
    "one thread per investigation"; threads abstraction would be over-engineered
    for the demo. Phase 6-full can add it by widening the FK.
  - jsonb[] of messages on investigations row: breaks 2s status polling cache
    (every chat turn would invalidate the row) and caps at 8k jsonb size.
  - TEXT[] for cited_chunk_ids: loses jsonb indexability; jsonb is cheap enough.
  - Redis-backed session store: adds infra dependency, violates STACK.md D-14
    (no Redis for v1).

Index rationale: (investigation_id, created_at) serves the GET /chat history
query exactly (WHERE investigation_id = ? ORDER BY created_at ASC). Plain
investigation_id-only index would leave created_at sort to an in-memory sort
on potentially thousands of rows in a long session; compound index is
effectively free at v1 scale.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE chat_messages (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            investigation_id   UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            role               TEXT NOT NULL CHECK (role IN ('user','assistant')),
            content            TEXT NOT NULL,
            cited_chunk_ids    JSONB DEFAULT '[]'::jsonb,
            created_at         TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX chat_messages_investigation_id_created_at_idx
            ON chat_messages (investigation_id, created_at);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_messages CASCADE;")
