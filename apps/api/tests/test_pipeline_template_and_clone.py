import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from aq_api.app import app
from aq_api.models import (
    ArchivePipelineResponse,
    ClonePipelineResponse,
    ListPipelinesResponse,
)
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "pipeline-template-test-"
PROJECT_SLUG_PREFIX = "pipeline-template-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live pipeline clone tests",
)

SOURCE_CONTRACTS: dict[str, dict[str, object]] = {
    "scope": {
        "contract_type": "scoping",
        "dod_items": [
            {
                "id": "scope-statement",
                "verification_method": "manual_review",
                "evidence_required": "scope statement document path under plans/",
                "acceptance_threshold": (
                    "scope names what's in and what's out; reviewed by Ghost"
                ),
            }
        ],
    },
    "build": {
        "contract_type": "coding-task",
        "dod_items": [
            {
                "id": "tests-pass",
                "verification_method": "command",
                "evidence_required": "pytest output captured to artifacts",
                "acceptance_threshold": (
                    "all tests pass; mypy --strict clean; ruff check clean"
                ),
            }
        ],
    },
    "verify": {
        "contract_type": "verification",
        "dod_items": [
            {
                "id": "claude-audit-pass",
                "verification_method": "review",
                "evidence_required": (
                    "claude per-story audit comment id on the parent ticket"
                ),
                "acceptance_threshold": "audit verdict APPROVED",
            }
        ],
    },
}


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _truncate_state(connection)
        yield connection
        _truncate_state(connection)


@pytest_asyncio.fixture()
async def async_client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client

    from aq_api._db import engine

    await engine.dispose()


def _truncate_state(conn: Connection[tuple[object, ...]]) -> None:
    actor_like = f"{ACTOR_PREFIX}%"
    project_like = f"{PROJECT_SLUG_PREFIX}%"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM audit_log
            WHERE authenticated_actor_id IN (
                SELECT id FROM actors WHERE name LIKE %s
            )
            """,
            (actor_like,),
        )
        cursor.execute(
            """
            DELETE FROM job_comments
            WHERE author_actor_id IN (SELECT id FROM actors WHERE name LIKE %s)
               OR job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
               )
            """,
            (actor_like, project_like),
        )
        cursor.execute(
            """
            DELETE FROM job_edges
            WHERE from_job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
               )
               OR to_job_id IN (
                    SELECT jobs.id
                    FROM jobs
                    JOIN projects ON projects.id = jobs.project_id
                    WHERE projects.slug LIKE %s
               )
            """,
            (project_like, project_like),
        )
        cursor.execute(
            """
            DELETE FROM jobs
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR project_id IN (
                    SELECT id FROM projects WHERE slug LIKE %s
               )
            """,
            (actor_like, project_like),
        )
        cursor.execute(
            """
            DELETE FROM labels
            WHERE project_id IN (
                SELECT id FROM projects WHERE slug LIKE %s
            )
            """,
            (project_like,),
        )
        cursor.execute(
            """
            DELETE FROM pipelines
            WHERE created_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
               OR project_id IN (
                    SELECT id FROM projects WHERE slug LIKE %s
               )
            """,
            (actor_like, project_like),
        )
        cursor.execute("DELETE FROM projects WHERE slug LIKE %s", (project_like,))
        cursor.execute(
            """
            DELETE FROM api_keys
            WHERE actor_id IN (SELECT id FROM actors WHERE name LIKE %s)
               OR revoked_by_actor_id IN (
                    SELECT id FROM actors WHERE name LIKE %s
               )
            """,
            (actor_like, actor_like),
        )
        cursor.execute("DELETE FROM actors WHERE name LIKE %s", (actor_like,))


def _insert_actor_with_key(conn: Connection[tuple[object, ...]]) -> tuple[UUID, str]:
    api_key = f"aq2_pipeline_template_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, 'human')
            RETURNING id
            """,
            (f"{ACTOR_PREFIX}{uuid.uuid4()}",),
        )
        row = cursor.fetchone()
        assert row is not None
        actor_id = row[0]
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
                f"{ACTOR_PREFIX}key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )
    return actor_id, api_key


def _insert_project(conn: Connection[tuple[object, ...]], actor_id: UUID) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                "Pipeline Template Test",
                f"{PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}",
                actor_id,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    project_id = row[0]
    assert isinstance(project_id, UUID)
    return project_id


def _insert_pipeline(
    conn: Connection[tuple[object, ...]],
    *,
    project_id: UUID,
    actor_id: UUID,
    name: str,
    is_template: bool = False,
    archived: bool = False,
) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO pipelines
                (project_id, name, is_template, archived_at, created_by_actor_id)
            VALUES
                (%s, %s, %s, CASE WHEN %s THEN now() ELSE NULL END, %s)
            RETURNING id
            """,
            (project_id, name, is_template, archived, actor_id),
        )
        row = cursor.fetchone()
    assert row is not None
    pipeline_id = row[0]
    assert isinstance(pipeline_id, UUID)
    return pipeline_id


def _insert_job(
    conn: Connection[tuple[object, ...]],
    *,
    pipeline_id: UUID,
    project_id: UUID,
    actor_id: UUID,
    title: str,
    labels: list[str],
    contract: dict[str, object],
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
                    labels,
                    created_by_actor_id
                )
            VALUES
                (%s, %s, 'ready', %s, %s, %s::jsonb, %s, %s)
            RETURNING id
            """,
            (
                pipeline_id,
                project_id,
                title,
                f"{title} description",
                json.dumps(contract),
                labels,
                actor_id,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    job_id = row[0]
    assert isinstance(job_id, UUID)
    return job_id


def _jobs_for_pipeline(
    conn: Connection[tuple[object, ...]],
    pipeline_id: UUID,
) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT title, description, state, contract, labels,
                   claimed_by_actor_id, claimed_at, claim_heartbeat_at
            FROM jobs
            WHERE pipeline_id = %s
            ORDER BY created_at, id
            """,
            (pipeline_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            "title": row[0],
            "description": row[1],
            "state": row[2],
            "contract": row[3],
            "labels": row[4],
            "claimed_by_actor_id": row[5],
            "claimed_at": row[6],
            "claim_heartbeat_at": row[7],
        }
        for row in rows
    ]


def _audit_rows(conn: Connection[tuple[object, ...]]) -> list[dict[str, object]]:
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


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.mark.asyncio
async def test_list_excludes_templates_and_archived_then_clone_copies_ready_jobs(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, api_key = _insert_actor_with_key(conn)
    project_id = _insert_project(conn, actor_id)
    template_id = _insert_pipeline(
        conn,
        project_id=project_id,
        actor_id=actor_id,
        name="ship-a-thing",
        is_template=True,
    )
    visible_id = _insert_pipeline(
        conn,
        project_id=project_id,
        actor_id=actor_id,
        name="visible",
    )
    archived_id = _insert_pipeline(
        conn,
        project_id=project_id,
        actor_id=actor_id,
        name="archived",
        archived=True,
    )
    for title, contract in SOURCE_CONTRACTS.items():
        _insert_job(
            conn,
            pipeline_id=template_id,
            project_id=project_id,
            actor_id=actor_id,
            title=title,
            labels=["area:web", "kind:template"],
            contract=contract,
        )

    headers = _auth_headers(api_key)
    list_response = await async_client.get(
        "/pipelines",
        headers=headers,
        params={"limit": 200},
    )

    assert list_response.status_code == 200
    listed = ListPipelinesResponse.model_validate(list_response.json())
    listed_ids = {pipeline.id for pipeline in listed.pipelines}
    assert visible_id in listed_ids
    assert template_id not in listed_ids
    assert archived_id not in listed_ids

    clone_response = await async_client.post(
        f"/pipelines/{template_id}/clone",
        headers=headers,
        json={"name": "customer-ship"},
    )

    assert clone_response.status_code == 200
    cloned = ClonePipelineResponse.model_validate(clone_response.json())
    assert cloned.pipeline.project_id == project_id
    assert cloned.pipeline.name == "customer-ship"
    assert cloned.pipeline.is_template is False
    assert cloned.pipeline.cloned_from_pipeline_id == template_id
    assert cloned.pipeline.archived_at is None
    assert cloned.pipeline.created_by_actor_id == actor_id
    assert cloned.pipeline.created_at.tzinfo == UTC
    assert len(cloned.jobs) == 3
    assert {job.state for job in cloned.jobs} == {"ready"}

    source_jobs = _jobs_for_pipeline(conn, template_id)
    clone_jobs = _jobs_for_pipeline(conn, cloned.pipeline.id)
    assert len(clone_jobs) == 3
    source_by_title = {str(job["title"]): job for job in source_jobs}
    clone_by_title = {str(job["title"]): job for job in clone_jobs}
    assert clone_by_title.keys() == source_by_title.keys()
    for title, source in source_by_title.items():
        clone = clone_by_title[title]
        assert clone["title"] == source["title"]
        assert clone["description"] == source["description"]
        assert clone["state"] == "ready"
        assert clone["contract"] == source["contract"]
        assert clone["labels"] == source["labels"]
        assert clone["claimed_by_actor_id"] is None
        assert clone["claimed_at"] is None
        assert clone["claim_heartbeat_at"] is None

    archive_response = await async_client.post(
        f"/pipelines/{cloned.pipeline.id}/archive",
        headers=headers,
    )

    assert archive_response.status_code == 200
    archived = ArchivePipelineResponse.model_validate(archive_response.json())
    assert archived.pipeline.id == cloned.pipeline.id
    assert archived.pipeline.archived_at is not None

    after_archive = await async_client.get(
        "/pipelines",
        headers=headers,
        params={"limit": 200},
    )
    assert after_archive.status_code == 200
    listed_after_archive = ListPipelinesResponse.model_validate(after_archive.json())
    listed_after_ids = {pipeline.id for pipeline in listed_after_archive.pipelines}
    assert visible_id in listed_after_ids
    assert cloned.pipeline.id not in listed_after_ids

    audit_rows = _audit_rows(conn)
    assert [row["op"] for row in audit_rows] == ["clone_pipeline", "archive_pipeline"]
    clone_audit = audit_rows[0]
    assert clone_audit["target_kind"] == "pipeline"
    assert clone_audit["target_id"] == str(cloned.pipeline.id)
    assert clone_audit["request_payload"] == {
        "source_id": str(template_id),
        "name": "customer-ship",
    }
    assert clone_audit["error_code"] is None
    assert clone_audit["response_payload"] is not None
    assert "contract" not in json.dumps(clone_audit["response_payload"])
