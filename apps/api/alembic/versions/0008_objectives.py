"""Add objectives table.

Revision ID: 0008_objectives
Revises: 0007_cap05
Create Date: 2026-05-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_objectives"
down_revision: str | Sequence[str] | None = "0007_cap05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTACHED_TO_KIND_CHECK = "attached_to_kind IN ('project','pipeline')"


def upgrade() -> None:
    op.create_table(
        "objectives",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("attached_to_kind", sa.Text(), nullable=False),
        sa.Column("attached_to_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=True),
        sa.Column("target_value", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            ATTACHED_TO_KIND_CHECK,
            name="objectives_attached_to_kind_check",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="objectives_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="objectives_pkey"),
    )
    op.create_index(
        "idx_objectives_attached",
        "objectives",
        ["attached_to_kind", "attached_to_id", "created_at"],
    )
    op.create_index(
        "idx_objectives_actor",
        "objectives",
        ["created_by_actor_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_objectives_actor", table_name="objectives")
    op.drop_index("idx_objectives_attached", table_name="objectives")
    op.drop_table("objectives")
