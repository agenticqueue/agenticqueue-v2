import json
import uuid
from uuid import UUID

import psycopg
from _db_cleanup import cleanup_cap03_state
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

ACTOR_PREFIX = "job-test-"
PROJECT_SLUG_PREFIX = "job-test-"
CONTRACT: dict[str, object] = {
    "contract_type": "coding-task",
    "dod_items": [
        {
            "id": "tests-pass",
            "verification_method": "command",
            "evidence_required": "pytest output",
            "acceptance_threshold": "all tests pass",
        }
    ],
}


def truncate_job_state(conn: Connection[tuple[object, ...]]) -> None:
    cleanup_cap03_state(
        conn,
        actor_name_prefix=ACTOR_PREFIX,
        project_slug_prefix=PROJECT_SLUG_PREFIX,
    )


def insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    *,
    name: str | None = None,
    key: str | None = None,
) -> tuple[UUID, str]:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    api_key = key or f"aq2_job_contract_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'human')
            RETURNING id
            """,
            (actor_name,),
        )
        actor_row = cursor.fetchone()
        assert actor_row is not None
        actor_id = actor_row[0]
        assert isinstance(actor_id, UUID)

        cursor.execute(
            """
            INSERT INTO api_keys
                (actor_id, name, key_hash, prefix, lookup_id)
            VALUES
                (%s, %s, %s, %s, %s)
            """,
            (
                actor_id,
                f"job-test-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def insert_project(
    conn: Connection[tuple[object, ...]],
    *,
    created_by_actor_id: UUID,
    slug: str | None = None,
) -> UUID:
    project_slug = slug or f"{PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            ("Job Test Project", project_slug, created_by_actor_id),
        )
        row = cursor.fetchone()
    assert row is not None
    project_id = row[0]
    assert isinstance(project_id, UUID)
    return project_id


def insert_pipeline(
    conn: Connection[tuple[object, ...]],
    *,
    project_id: UUID,
    created_by_actor_id: UUID,
    name: str | None = None,
) -> UUID:
    pipeline_name = name or f"job-test-pipeline-{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO pipelines (project_id, name, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (project_id, pipeline_name, created_by_actor_id),
        )
        row = cursor.fetchone()
    assert row is not None
    pipeline_id = row[0]
    assert isinstance(pipeline_id, UUID)
    return pipeline_id


def insert_job(
    conn: Connection[tuple[object, ...]],
    *,
    pipeline_id: UUID,
    project_id: UUID,
    created_by_actor_id: UUID,
    title: str,
    state: str = "ready",
    contract: dict[str, object] | None = None,
) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO jobs
                (
                    pipeline_id,
                    project_id,
                    state,
                    title,
                    description,
                    contract,
                    created_by_actor_id
                )
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
                pipeline_id,
                project_id,
                state,
                title,
                f"{title} description",
                json.dumps(contract or CONTRACT),
                created_by_actor_id,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    job_id = row[0]
    assert isinstance(job_id, UUID)
    return job_id


def auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def audit_rows(conn: Connection[tuple[object, ...]]) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT op, target_kind, target_id, request_payload,
                   response_payload, error_code
            FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            ORDER BY ts ASC, id ASC
            """,
            (f"{ACTOR_PREFIX}%",),
        )
        rows = cursor.fetchall()
    return [
        {
            "op": row[0],
            "target_kind": row[1],
            "target_id": str(row[2]) if row[2] is not None else None,
            "request_payload": row[3],
            "response_payload": row[4],
            "error_code": row[5],
        }
        for row in rows
    ]


def job_row(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
) -> dict[str, object]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cursor:
        cursor.execute(
            """
            SELECT id, pipeline_id, project_id, state, title, description,
                   contract, labels, claimed_by_actor_id, claimed_at,
                   claim_heartbeat_at
            FROM jobs
            WHERE id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return dict(row)
