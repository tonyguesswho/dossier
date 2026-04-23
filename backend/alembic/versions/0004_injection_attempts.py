"""Add injection_attempts table for GUARD-02 prompt-injection quarantine.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-23

Schema source: .planning/phases/03-langgraph-agent-aws-deployment/03-CONTEXT.md D-07.

Chunks flagged as prompt-injection by the Haiku 4.5 classifier inside the
ingest_and_embed node are written here instead of source_chunks. They never
reach retrieval top-k, the synthesizer, or the final brief. The table persists
raw payloads for post-hoc red-team analysis (D-07: synchronous block > async
flag because a leaked injection corrupts the user-facing brief).

Security note: raw_payload is TEXT (unbounded) — truncating would destroy
red-team evidence value. DB storage cost is low relative to forensic value
(T-03-01-03 accepted).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE injection_attempts (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            investigation_id UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            url              TEXT NOT NULL,
            raw_payload      TEXT NOT NULL,
            verdict          TEXT NOT NULL,
            reason           TEXT NOT NULL,
            created_at       TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX injection_attempts_investigation_id_idx
            ON injection_attempts (investigation_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS injection_attempts;")
