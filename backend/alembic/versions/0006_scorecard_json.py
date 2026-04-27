"""Add scorecard_json to investigations.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-27
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE investigations ADD COLUMN IF NOT EXISTS scorecard_json JSONB NULL;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE investigations DROP COLUMN IF EXISTS scorecard_json;")
