"""Create cap #3 entity tables, indexes, and seed rows.

Revision ID: 0004_cap03_entities
Revises: 0003_api_key_lookup_id
Create Date: 2026-04-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_cap03_entities"
down_revision: str | None = "0003_api_key_lookup_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PROFILE_IDS: dict[str, str] = {
    "coding-task": "3d15c273-5308-49bf-8ad6-dcc60b4c44ce",
    "bug-fix": "56153aa4-becf-4f1a-adf4-0f656ebf9e58",
    "docs-task": "24b84372-0957-4d72-86b0-56b347d3f7bd",
    "research-decision": "0f165db6-0c37-4c85-9d60-3cc4841dce54",
}

SEED_WORKFLOW_ID = "60e100f1-efc3-4ee6-8f03-db6a7f3dd7c8"
SEED_WORKFLOW_STEPS: list[tuple[str, str, int, str]] = [
    (
        "9c00c950-f5f7-4c70-a4b5-f1f2a8c553ec",
        "scope",
        1,
        PROFILE_IDS["research-decision"],
    ),
    (
        "85af2824-5f99-4f8f-9d7d-c1c3c0a4c6ab",
        "build",
        2,
        PROFILE_IDS["coding-task"],
    ),
    (
        "4dc2f6b9-b6a2-4d50-81db-7654e592d219",
        "verify",
        3,
        PROFILE_IDS["bug-fix"],
    ),
]


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="projects_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="projects_pkey"),
        sa.UniqueConstraint("slug", name="projects_slug_key"),
    )

    op.create_table(
        "labels",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="labels_project_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="labels_pkey"),
    )
    op.create_index(
        "labels_project_name_active_uniq",
        "labels",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "workflows",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "is_archived",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "supersedes_workflow_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="workflows_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_workflow_id"],
            ["workflows.id"],
            name="workflows_supersedes_workflow_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="workflows_pkey"),
        sa.UniqueConstraint("slug", "version", name="workflows_slug_version_key"),
    )

    op.create_table(
        "contract_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "required_dod_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "schema",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="contract_profiles_pkey"),
        sa.UniqueConstraint(
            "name",
            "version",
            name="contract_profiles_name_version_key",
        ),
    )

    op.create_table(
        "workflow_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "default_contract_profile_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "step_edges",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="workflow_steps_workflow_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["default_contract_profile_id"],
            ["contract_profiles.id"],
            name="workflow_steps_default_contract_profile_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="workflow_steps_pkey"),
        sa.UniqueConstraint(
            "workflow_id",
            "ordinal",
            name="workflow_steps_workflow_ordinal_key",
        ),
    )

    op.create_table(
        "pipelines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "instantiated_from_workflow_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("instantiated_from_workflow_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="pipelines_project_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instantiated_from_workflow_id"],
            ["workflows.id"],
            name="pipelines_instantiated_from_workflow_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="pipelines_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pipelines_pkey"),
        sa.UniqueConstraint("id", "project_id", name="pipelines_id_project_id_uniq"),
    )

    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("pipeline_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("contract_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "instantiated_from_step_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "labels",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("claimed_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by_actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            (
                "state IN "
                "('draft','ready','in_progress','done','failed',"
                "'blocked','pending_review','cancelled')"
            ),
            name="jobs_state_check",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_id", "project_id"],
            ["pipelines.id", "pipelines.project_id"],
            name="jobs_pipeline_id_project_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="jobs_project_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_profile_id"],
            ["contract_profiles.id"],
            name="jobs_contract_profile_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instantiated_from_step_id"],
            ["workflow_steps.id"],
            name="jobs_instantiated_from_step_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["claimed_by_actor_id"],
            ["actors.id"],
            name="jobs_claimed_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_actor_id"],
            ["actors.id"],
            name="jobs_created_by_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="jobs_pkey"),
    )
    op.create_index(
        "idx_jobs_labels_gin",
        "jobs",
        ["labels"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_jobs_state_project_created",
        "jobs",
        ["state", "project_id", "created_at"],
        postgresql_where=sa.text("state = 'ready'"),
    )

    op.create_table(
        "job_edges",
        sa.Column("from_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edge_type", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "edge_type IN ('gated_on','parent_of','sequence_next')",
            name="job_edges_edge_type_check",
        ),
        sa.ForeignKeyConstraint(
            ["from_job_id"],
            ["jobs.id"],
            name="job_edges_from_job_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_job_id"],
            ["jobs.id"],
            name="job_edges_to_job_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "from_job_id",
            "to_job_id",
            "edge_type",
            name="job_edges_pkey",
        ),
    )

    op.create_table(
        "job_comments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="job_comments_job_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_actor_id"],
            ["actors.id"],
            name="job_comments_author_actor_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="job_comments_pkey"),
    )

    op.execute(
        """
        INSERT INTO contract_profiles
            (id, name, version, description, required_dod_ids, schema)
        VALUES
            (
                '3d15c273-5308-49bf-8ad6-dcc60b4c44ce',
                'coding-task',
                1,
                'Default coding task contract profile.',
                '["DOD-CODE-01"]'::jsonb,
                '{"type":"object","properties":{"summary":{"type":"string"}}}'::jsonb
            ),
            (
                '56153aa4-becf-4f1a-adf4-0f656ebf9e58',
                'bug-fix',
                1,
                'Default bug fix contract profile.',
                '["DOD-BUG-01"]'::jsonb,
                '{"type":"object","properties":{"root_cause":{"type":"string"}}}'::jsonb
            ),
            (
                '24b84372-0957-4d72-86b0-56b347d3f7bd',
                'docs-task',
                1,
                'Default docs task contract profile.',
                '["DOD-DOC-01"]'::jsonb,
                '{"type":"object","properties":{"sections":{"type":"array"}}}'::jsonb
            ),
            (
                '0f165db6-0c37-4c85-9d60-3cc4841dce54',
                'research-decision',
                1,
                'Default research decision contract profile.',
                '["DOD-RES-01"]'::jsonb,
                '{"type":"object","properties":{"decision":{"type":"string"}}}'::jsonb
            )
        """
    )

    op.execute(
        """
        INSERT INTO workflows
            (
                id,
                slug,
                name,
                version,
                is_archived,
                supersedes_workflow_id,
                created_by_actor_id
            )
        VALUES
            (
                '60e100f1-efc3-4ee6-8f03-db6a7f3dd7c8',
                'ship-a-thing',
                'Ship A Thing',
                1,
                false,
                NULL,
                NULL
            )
        """
    )

    op.execute(
        """
        INSERT INTO workflow_steps
            (id, workflow_id, name, ordinal, default_contract_profile_id, step_edges)
        VALUES
            (
                '9c00c950-f5f7-4c70-a4b5-f1f2a8c553ec',
                '60e100f1-efc3-4ee6-8f03-db6a7f3dd7c8',
                'scope',
                1,
                '0f165db6-0c37-4c85-9d60-3cc4841dce54',
                '{}'::jsonb
            ),
            (
                '85af2824-5f99-4f8f-9d7d-c1c3c0a4c6ab',
                '60e100f1-efc3-4ee6-8f03-db6a7f3dd7c8',
                'build',
                2,
                '3d15c273-5308-49bf-8ad6-dcc60b4c44ce',
                '{}'::jsonb
            ),
            (
                '4dc2f6b9-b6a2-4d50-81db-7654e592d219',
                '60e100f1-efc3-4ee6-8f03-db6a7f3dd7c8',
                'verify',
                3,
                '56153aa4-becf-4f1a-adf4-0f656ebf9e58',
                '{}'::jsonb
            )
        """
    )


def downgrade() -> None:
    op.drop_table("job_comments")
    op.drop_table("job_edges")
    op.drop_index("idx_jobs_state_project_created", table_name="jobs")
    op.drop_index("idx_jobs_labels_gin", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("pipelines")
    op.drop_table("workflow_steps")
    op.drop_table("contract_profiles")
    op.drop_table("workflows")
    op.drop_index("labels_project_name_active_uniq", table_name="labels")
    op.drop_table("labels")
    op.drop_table("projects")
