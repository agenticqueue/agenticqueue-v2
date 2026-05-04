"""Add components table.

Revision ID: 0010_components
Revises: 0009_visuals
Create Date: 2026-05-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_components"
down_revision: str | Sequence[str] | None = "0009_visuals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTACHED_TO_KIND_CHECK = "attached_to_kind IN ('project','pipeline')"


def upgrade() -> None:
    op.create_table(
        "components",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("attached_to_kind", sa.Text(), nullable=False),
        sa.Column("attached_to_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("access_path", sa.Text(), nullable=False),
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
            name="components_attached_to_kind_check",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="components_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="components_pkey"),
    )
    op.create_index(
        "idx_components_attached",
        "components",
        ["attached_to_kind", "attached_to_id", "created_at"],
    )
    op.create_index(
        "idx_components_actor",
        "components",
        ["created_by_actor_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_components_actor", table_name="components")
    op.drop_index("idx_components_attached", table_name="components")
    op.drop_table("components")
