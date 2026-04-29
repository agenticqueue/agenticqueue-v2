"""Consolidate cap 03 workflow schema into template pipelines.

Revision ID: 0005_cap0305
Revises: 0004_cap03_entities
Create Date: 2026-04-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_cap0305"
down_revision: str | Sequence[str] | None = "0004_cap03_entities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PROFILE_IDS: dict[str, str] = {
    "coding-task": "3d15c273-5308-49bf-8ad6-dcc60b4c44ce",
    "bug-fix": "56153aa4-becf-4f1a-adf4-0f656ebf9e58",
    "docs-task": "24b84372-0957-4d72-86b0-56b347d3f7bd",
    "research-decision": "0f165db6-0c37-4c85-9d60-3cc4841dce54",
}

WORKFLOW_ID = "60e100f1-efc3-4ee6-8f03-db6a7f3dd7c8"
STEP_SCOPE_ID = "9c00c950-f5f7-4c70-a4b5-f1f2a8c553ec"
STEP_BUILD_ID = "85af2824-5f99-4f8f-9d7d-c1c3c0a4c6ab"
STEP_VERIFY_ID = "4dc2f6b9-b6a2-4d50-81db-7654e592d219"


def upgrade() -> None:
    _assert_job_edges_check_shape()

    op.add_column(
        "pipelines",
        sa.Column(
            "is_template",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "pipelines",
        sa.Column(
            "cloned_from_pipeline_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "pipelines",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "pipelines_cloned_from_pipeline_id_fkey",
        "pipelines",
        "pipelines",
        ["cloned_from_pipeline_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "jobs",
        sa.Column(
            "contract",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.drop_constraint(
        "jobs_instantiated_from_step_id_fkey", "jobs", type_="foreignkey"
    )
    op.drop_constraint("jobs_contract_profile_id_fkey", "jobs", type_="foreignkey")
    op.drop_constraint("jobs_pipeline_id_project_id_fkey", "jobs", type_="foreignkey")
    op.drop_constraint(
        "pipelines_instantiated_from_workflow_id_fkey",
        "pipelines",
        type_="foreignkey",
    )
    op.drop_constraint("pipelines_id_project_id_uniq", "pipelines", type_="unique")

    op.drop_column("jobs", "instantiated_from_step_id")
    op.drop_column("jobs", "contract_profile_id")
    op.drop_column("pipelines", "instantiated_from_workflow_version")
    op.drop_column("pipelines", "instantiated_from_workflow_id")

    _seed_ship_a_thing_template_pipeline()

    op.drop_table("workflow_steps")
    op.drop_table("workflows")
    op.drop_table("contract_profiles")


def downgrade() -> None:
    op.drop_constraint(
        "pipelines_cloned_from_pipeline_id_fkey", "pipelines", type_="foreignkey"
    )
    _delete_ship_a_thing_template_pipeline()

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
            "default_contract_profile_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "step_edges",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["default_contract_profile_id"],
            ["contract_profiles.id"],
            name="workflow_steps_default_contract_profile_id_fkey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="workflow_steps_workflow_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="workflow_steps_pkey"),
        sa.UniqueConstraint(
            "workflow_id", "ordinal", name="workflow_steps_workflow_ordinal_key"
        ),
    )
    _restore_cap03_seed_rows()

    op.add_column(
        "pipelines",
        sa.Column(
            "instantiated_from_workflow_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "pipelines",
        sa.Column("instantiated_from_workflow_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("contract_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "instantiated_from_step_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.execute(
        f"""
        UPDATE jobs
        SET contract_profile_id = '{PROFILE_IDS["coding-task"]}'
        WHERE contract_profile_id IS NULL
        """
    )
    op.alter_column(
        "jobs",
        "contract_profile_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_unique_constraint(
        "pipelines_id_project_id_uniq", "pipelines", ["id", "project_id"]
    )
    op.create_foreign_key(
        "pipelines_instantiated_from_workflow_id_fkey",
        "pipelines",
        "workflows",
        ["instantiated_from_workflow_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "jobs_pipeline_id_project_id_fkey",
        "jobs",
        "pipelines",
        ["pipeline_id", "project_id"],
        ["id", "project_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "jobs_contract_profile_id_fkey",
        "jobs",
        "contract_profiles",
        ["contract_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "jobs_instantiated_from_step_id_fkey",
        "jobs",
        "workflow_steps",
        ["instantiated_from_step_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_column("jobs", "contract")
    op.drop_column("pipelines", "archived_at")
    op.drop_column("pipelines", "cloned_from_pipeline_id")
    op.drop_column("pipelines", "is_template")


def _assert_job_edges_check_shape() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            constraint_def text;
        BEGIN
            SELECT pg_get_constraintdef(oid)
              INTO constraint_def
              FROM pg_constraint
             WHERE conname = 'job_edges_edge_type_check';

            IF constraint_def IS NULL
               OR constraint_def NOT LIKE '%gated_on%'
               OR constraint_def NOT LIKE '%parent_of%'
               OR constraint_def NOT LIKE '%sequence_next%'
               OR constraint_def LIKE '%instantiated_from%'
               OR constraint_def LIKE '%job_references%'
            THEN
                RAISE EXCEPTION
                    'job_edges_edge_type_check drifted before cap 03.5 migration: %',
                    constraint_def;
            END IF;
        END $$;
        """
    )


def _seed_ship_a_thing_template_pipeline() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            v_founder_id uuid;
            v_project_id uuid;
            v_workflow_id uuid;
            v_pipeline_id uuid;
        BEGIN
            SELECT id
              INTO v_workflow_id
              FROM workflows
             WHERE slug = 'ship-a-thing'
               AND version = 1
             ORDER BY created_at, id
             LIMIT 1;

            IF v_workflow_id IS NULL THEN
                RETURN;
            END IF;

            SELECT id
              INTO v_founder_id
              FROM actors
             WHERE name = 'founder'
             ORDER BY created_at, id
             LIMIT 1;

            IF v_founder_id IS NULL THEN
                RETURN;
            END IF;

            SELECT id
              INTO v_project_id
              FROM projects
             WHERE created_by_actor_id = v_founder_id
             ORDER BY created_at, id
             LIMIT 1;

            IF v_project_id IS NULL THEN
                RETURN;
            END IF;

            SELECT id
              INTO v_pipeline_id
              FROM pipelines
             WHERE name = 'ship-a-thing'
               AND is_template = true
             ORDER BY created_at, id
             LIMIT 1;

            IF v_pipeline_id IS NULL THEN
                INSERT INTO pipelines (
                    project_id,
                    name,
                    created_by_actor_id,
                    is_template
                )
                VALUES (
                    v_project_id,
                    'ship-a-thing',
                    v_founder_id,
                    true
                )
                RETURNING id INTO v_pipeline_id;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM jobs WHERE pipeline_id = v_pipeline_id
            ) THEN
                INSERT INTO jobs (
                    pipeline_id,
                    project_id,
                    state,
                    title,
                    description,
                    contract,
                    created_by_actor_id
                )
                SELECT
                    v_pipeline_id,
                    v_project_id,
                    'ready',
                    workflow_steps.name,
                    NULL,
                    CASE workflow_steps.name
                        WHEN 'scope' THEN jsonb_build_object(
                            'contract_type',
                            'scoping',
                            'dod_items',
                            jsonb_build_array(
                                jsonb_build_object(
                                    'id',
                                    'scope-statement',
                                    'verification_method',
                                    'manual_review',
                                    'evidence_required',
                                    'scope statement document path under plans/',
                                    'acceptance_threshold',
                                    'scope names what''s in and what''s out; '
                                    || 'reviewed by Ghost'
                                )
                            )
                        )
                        WHEN 'build' THEN jsonb_build_object(
                            'contract_type',
                            'coding-task',
                            'dod_items',
                            jsonb_build_array(
                                jsonb_build_object(
                                    'id',
                                    'tests-pass',
                                    'verification_method',
                                    'command',
                                    'evidence_required',
                                    'pytest output captured to artifacts',
                                    'acceptance_threshold',
                                    'all tests pass; mypy --strict clean; '
                                    || 'ruff check clean'
                                ),
                                jsonb_build_object(
                                    'id',
                                    'commit-pushed',
                                    'verification_method',
                                    'command',
                                    'evidence_required',
                                    'git rev-parse HEAD',
                                    'acceptance_threshold',
                                    'branch tip pushed to origin'
                                )
                            )
                        )
                        WHEN 'verify' THEN jsonb_build_object(
                            'contract_type',
                            'verification',
                            'dod_items',
                            jsonb_build_array(
                                jsonb_build_object(
                                    'id',
                                    'claude-audit-pass',
                                    'verification_method',
                                    'review',
                                    'evidence_required',
                                    'claude per-story audit comment id '
                                    || 'on the parent ticket',
                                    'acceptance_threshold',
                                    'audit verdict APPROVED'
                                )
                            )
                        )
                        ELSE jsonb_build_object(
                            'contract_type', 'coding-task',
                            'dod_items', '[]'::jsonb
                        )
                    END,
                    v_founder_id
                FROM workflow_steps
                WHERE workflow_steps.workflow_id = v_workflow_id
                ORDER BY workflow_steps.ordinal;
            END IF;
        END $$;
        """
    )


def _delete_ship_a_thing_template_pipeline() -> None:
    op.execute(
        """
        DELETE FROM jobs
        WHERE pipeline_id IN (
            SELECT id
              FROM pipelines
             WHERE name = 'ship-a-thing'
               AND is_template = true
               AND cloned_from_pipeline_id IS NULL
        );

        DELETE FROM pipelines
        WHERE name = 'ship-a-thing'
          AND is_template = true
          AND cloned_from_pipeline_id IS NULL;
        """
    )


def _restore_cap03_seed_rows() -> None:
    op.execute(
        f"""
        INSERT INTO contract_profiles
            (id, name, version, description, required_dod_ids, schema)
        VALUES
            (
                '{PROFILE_IDS["coding-task"]}',
                'coding-task',
                1,
                'Default coding task contract profile.',
                '["DOD-CODE-01"]'::jsonb,
                '{{"type":"object","properties":{{"summary":{{"type":"string"}}}}}}'::jsonb
            ),
            (
                '{PROFILE_IDS["bug-fix"]}',
                'bug-fix',
                1,
                'Default bug fix contract profile.',
                '["DOD-BUG-01"]'::jsonb,
                '{{"type":"object","properties":{{"root_cause":{{"type":"string"}}}}}}'::jsonb
            ),
            (
                '{PROFILE_IDS["docs-task"]}',
                'docs-task',
                1,
                'Default docs task contract profile.',
                '["DOD-DOC-01"]'::jsonb,
                '{{"type":"object","properties":{{"sections":{{"type":"array"}}}}}}'::jsonb
            ),
            (
                '{PROFILE_IDS["research-decision"]}',
                'research-decision',
                1,
                'Default research decision contract profile.',
                '["DOD-RES-01"]'::jsonb,
                '{{"type":"object","properties":{{"decision":{{"type":"string"}}}}}}'::jsonb
            )
        ON CONFLICT (id) DO NOTHING;

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
            '{WORKFLOW_ID}',
            'ship-a-thing',
            'Ship A Thing',
            1,
            false,
            NULL,
            NULL
            )
        ON CONFLICT (slug, version) DO NOTHING;

        INSERT INTO workflow_steps (
            id,
            workflow_id,
            name,
            ordinal,
            default_contract_profile_id,
            step_edges
        )
        VALUES
            (
                '{STEP_SCOPE_ID}',
                '{WORKFLOW_ID}',
                'scope',
                1,
                '{PROFILE_IDS["research-decision"]}',
                '{{}}'::jsonb
            ),
            (
                '{STEP_BUILD_ID}',
                '{WORKFLOW_ID}',
                'build',
                2,
                '{PROFILE_IDS["coding-task"]}',
                '{{}}'::jsonb
            ),
            (
                '{STEP_VERIFY_ID}',
                '{WORKFLOW_ID}',
                'verify',
                3,
                '{PROFILE_IDS["bug-fix"]}',
                '{{}}'::jsonb
            )
        ON CONFLICT (id) DO NOTHING;
        """
    )
