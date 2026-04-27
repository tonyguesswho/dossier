"""Add the injection_attempts table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-23
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
