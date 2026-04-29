"""Add cap #4 claim indexes and reserved system actor.

Revision ID: 0006_cap04
Revises: 0005_cap0305
Create Date: 2026-04-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_cap04"
down_revision: str | Sequence[str] | None = "0005_cap0305"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYSTEM_ACTOR_NAME = "aq-system-sweeper"
SYSTEM_ACTOR_SEED_SQL = f"""
INSERT INTO actors (name, kind)
SELECT '{SYSTEM_ACTOR_NAME}', 'script'
WHERE NOT EXISTS (
  SELECT 1 FROM actors
  WHERE name = '{SYSTEM_ACTOR_NAME}' AND deactivated_at IS NULL
);
"""


def upgrade() -> None:
    op.create_index(
        "idx_jobs_in_progress_heartbeat",
        "jobs",
        ["claim_heartbeat_at", "id"],
        postgresql_where=sa.text("state = 'in_progress'"),
    )
    op.execute(SYSTEM_ACTOR_SEED_SQL)


def downgrade() -> None:
    op.drop_index("idx_jobs_in_progress_heartbeat", table_name="jobs")
    op.execute(
        f"""
        DELETE FROM actors
        WHERE name = '{SYSTEM_ACTOR_NAME}'
          AND kind = 'script'
          AND deactivated_at IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM api_keys
            WHERE api_keys.actor_id = actors.id
          )
          AND NOT EXISTS (
            SELECT 1 FROM audit_log
            WHERE audit_log.authenticated_actor_id = actors.id
          );
        """
    )
