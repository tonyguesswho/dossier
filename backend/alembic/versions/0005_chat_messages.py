"""Add the chat_messages table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-23
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
