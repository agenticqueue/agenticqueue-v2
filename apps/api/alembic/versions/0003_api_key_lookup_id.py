"""Add API key lookup ids.

Revision ID: 0003_api_key_lookup_id
Revises: 0002_actors_apikeys_audit
Create Date: 2026-04-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_api_key_lookup_id"
down_revision: str | None = "0002_actors_apikeys_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("lookup_id", postgresql.BYTEA(), nullable=True),
    )
    # Existing pre-lookup keys cannot be authenticated without plaintext.
    # Backfill unique invalid ids so the schema can tighten to NOT NULL.
    op.execute(
        "UPDATE api_keys "
        "SET lookup_id = decode(md5(id::text), 'hex') "
        "WHERE lookup_id IS NULL"
    )
    op.alter_column("api_keys", "lookup_id", nullable=False)
    op.drop_index("api_keys_prefix_idx", table_name="api_keys")
    op.create_index(
        "api_keys_lookup_id_uniq",
        "api_keys",
        ["lookup_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("api_keys_lookup_id_uniq", table_name="api_keys")
    op.create_index("api_keys_prefix_idx", "api_keys", ["prefix"])
    op.drop_column("api_keys", "lookup_id")
