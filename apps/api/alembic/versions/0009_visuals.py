"""Add visuals table.

Revision ID: 0009_visuals
Revises: 0008_objectives
Create Date: 2026-05-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_visuals"
down_revision: str | Sequence[str] | None = "0008_objectives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTACHED_TO_KIND_CHECK = (
    "attached_to_kind IN ('project','pipeline','job','decision','learning')"
)
VISUAL_TYPE_CHECK = "type IN ('mermaid','graphviz','plantuml','vega-lite','ascii')"


def upgrade() -> None:
    op.create_table(
        "visuals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("attached_to_kind", sa.Text(), nullable=False),
        sa.Column("attached_to_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
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
            name="visuals_attached_to_kind_check",
        ),
        sa.CheckConstraint(VISUAL_TYPE_CHECK, name="visuals_type_check"),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="visuals_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="visuals_pkey"),
    )
    op.create_index(
        "idx_visuals_attached",
        "visuals",
        ["attached_to_kind", "attached_to_id", "created_at"],
    )
    op.create_index(
        "idx_visuals_actor",
        "visuals",
        ["created_by_actor_id", "created_at"],
    )
    op.create_index("idx_visuals_type", "visuals", ["type"])


def downgrade() -> None:
    op.drop_index("idx_visuals_type", table_name="visuals")
    op.drop_index("idx_visuals_actor", table_name="visuals")
    op.drop_index("idx_visuals_attached", table_name="visuals")
    op.drop_table("visuals")
