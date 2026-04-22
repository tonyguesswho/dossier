"""Add re_run_of FK to investigations for LIB-02 re-run linkage.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-22

Schema source: .planning/phases/02-single-pass-rag-pipeline/02-CONTEXT.md D-18 / D-25.

Phase 2 additions:
  D-18 / D-25: investigations.re_run_of UUID REFERENCES investigations(id) — nullable.
               Both the original and the re-run row are preserved so:
                 - VCs can compare old vs. new after web drift
                 - Phase 6 shareable links keep resolving to the exact historical brief.
               Partial index added on rows WHERE re_run_of IS NOT NULL for cheap lookup.

Rejected alternatives:
  - Mutate the existing investigation row in place: destroys historical comparison.
  - Copy brief into a new "brief_versions" table: over-normalized for a 14-day build.
  - Full (non-partial) index: wasteful — the vast majority of rows have re_run_of = NULL.
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
