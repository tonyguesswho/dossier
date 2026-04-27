"""Add the re_run_of foreign key to investigations.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-22
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE investigations
            ADD COLUMN re_run_of UUID REFERENCES investigations(id)
        """
    )
    op.execute(
        """
        CREATE INDEX investigations_re_run_of_idx
            ON investigations (re_run_of)
            WHERE re_run_of IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS investigations_re_run_of_idx")
    op.execute("ALTER TABLE investigations DROP COLUMN IF EXISTS re_run_of")
