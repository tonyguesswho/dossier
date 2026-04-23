"""Change investigations.re_run_of FK from NO ACTION to ON DELETE SET NULL.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-23

UAT Phase 2 Test 3 surfaced a blocker: deleting a parent investigation that has
been re-run raises psycopg.ForeignKeyViolation because the child row's re_run_of
column still references the parent under the default NO ACTION rule.

Fix: recreate the FK with ON DELETE SET NULL so the re-run row survives as its
own valid investigation (preserving its brief + claims + sources) while the
parent link becomes NULL. Cascade-delete would be wrong — re-runs are
independent investigations, not dependent children.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE investigations
            DROP CONSTRAINT investigations_re_run_of_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE investigations
            ADD CONSTRAINT investigations_re_run_of_fkey
            FOREIGN KEY (re_run_of)
            REFERENCES investigations(id)
            ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE investigations
            DROP CONSTRAINT investigations_re_run_of_fkey
        """
    )
    op.execute(
        """
        ALTER TABLE investigations
            ADD CONSTRAINT investigations_re_run_of_fkey
            FOREIGN KEY (re_run_of)
            REFERENCES investigations(id)
        """
    )
