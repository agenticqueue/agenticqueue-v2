"""Empty baseline.

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-27 00:00:00.000000
"""

from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
