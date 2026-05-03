"""Add cap #5 decisions and learnings tables.

Revision ID: 0007_cap05
Revises: 0006_cap04
Create Date: 2026-04-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_cap05"
down_revision: str | Sequence[str] | None = "0006_cap04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTACHED_TO_KIND_CHECK = "attached_to_kind IN ('job','pipeline','project')"


def upgrade() -> None:
    op.create_table(
        "decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("attached_to_kind", sa.Text(), nullable=False),
        sa.Column("attached_to_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("supersedes_decision_id", postgresql.UUID(as_uuid=True)),
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
            name="decisions_attached_to_kind_check",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id"],
            ["decisions.id"],
            name="decisions_supersedes_decision_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="decisions_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="decisions_pkey"),
    )
    op.create_index(
        "idx_decisions_attached",
        "decisions",
        ["attached_to_kind", "attached_to_id", "created_at"],
    )
    op.create_index(
        "idx_decisions_actor",
        "decisions",
        ["created_by_actor_id", "created_at"],
    )

    op.create_table(
        "learnings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("attached_to_kind", sa.Text(), nullable=False),
        sa.Column("attached_to_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
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
            name="learnings_attached_to_kind_check",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="learnings_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="learnings_pkey"),
    )
    op.create_index(
        "idx_learnings_attached",
        "learnings",
        ["attached_to_kind", "attached_to_id", "created_at"],
    )
    op.create_index(
        "idx_learnings_actor",
        "learnings",
        ["created_by_actor_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_learnings_actor", table_name="learnings")
    op.drop_index("idx_learnings_attached", table_name="learnings")
    op.drop_table("learnings")
    op.drop_index("idx_decisions_actor", table_name="decisions")
    op.drop_index("idx_decisions_attached", table_name="decisions")
    op.drop_table("decisions")
