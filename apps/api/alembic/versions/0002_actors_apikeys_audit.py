"""Create actors, api keys, and audit log.

Revision ID: 0002_actors_apikeys_audit
Revises: 0001_initial
Create Date: 2026-04-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_actors_apikeys_audit"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "actors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('human','agent','script','routine')",
            name="actors_kind_check",
        ),
        sa.PrimaryKeyConstraint("id", name="actors_pkey"),
    )
    op.execute(
        "CREATE UNIQUE INDEX actors_name_active_uniq "
        "ON actors (name) WHERE deactivated_at IS NULL"
    )

    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_actor_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_by_actor_id IS NULL)",
            name="api_keys_revoked_fields_check",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["actors.id"],
            name="api_keys_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_actor_id"],
            ["actors.id"],
            name="api_keys_revoked_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="api_keys_pkey"),
    )
    op.execute(
        "CREATE INDEX api_keys_actor_active_idx "
        "ON api_keys (actor_id) WHERE revoked_at IS NULL"
    )
    op.create_index("api_keys_prefix_idx", "api_keys", ["prefix"])

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column(
            "authenticated_actor_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("claimed_actor_identity", sa.Text(), nullable=True),
        sa.Column("target_kind", sa.Text(), nullable=True),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "response_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["authenticated_actor_id"],
            ["actors.id"],
            name="audit_log_authenticated_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="audit_log_pkey"),
    )
    op.execute("CREATE INDEX audit_log_ts_idx ON audit_log (ts DESC)")
    op.execute(
        "CREATE INDEX audit_log_actor_ts_idx "
        "ON audit_log (authenticated_actor_id, ts DESC)"
    )
    op.execute("CREATE INDEX audit_log_op_ts_idx ON audit_log (op, ts DESC)")
    op.execute(
        "CREATE INDEX audit_log_target_idx "
        "ON audit_log (target_kind, target_id) WHERE target_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("audit_log_target_idx", table_name="audit_log")
    op.drop_index("audit_log_op_ts_idx", table_name="audit_log")
    op.drop_index("audit_log_actor_ts_idx", table_name="audit_log")
    op.drop_index("audit_log_ts_idx", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("api_keys_prefix_idx", table_name="api_keys")
    op.drop_index("api_keys_actor_active_idx", table_name="api_keys")
    op.drop_table("api_keys")

    op.drop_index("actors_name_active_uniq", table_name="actors")
    op.drop_table("actors")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
