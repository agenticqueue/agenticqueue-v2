import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _db_cleanup import cleanup_cap03_state
from aq_api.app import app
from aq_api.models import InstantiatePipelineResponse
from aq_api.services.auth import (
    DISPLAY_PREFIX_LENGTH,
    PASSWORD_HASHER,
    lookup_id_for_key,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")
ACTOR_PREFIX = "instantiate-test-"
PROJECT_SLUG_PREFIX = "instantiate-test-"

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live instantiate tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        _truncate_cap03_state(connection)
        yield connection
        _truncate_cap03_state(connection)


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


def _truncate_cap03_state(conn: Connection[tuple[object, ...]]) -> None:
    cleanup_cap03_state(
        conn,
        actor_name_prefix=ACTOR_PREFIX,
        project_slug_prefix=PROJECT_SLUG_PREFIX,
    )


def _insert_actor_with_key(
    conn: Connection[tuple[object, ...]],
    *,
    name: str | None = None,
    kind: str = "human",
    key: str | None = None,
) -> tuple[UUID, str]:
    actor_name = name or f"{ACTOR_PREFIX}{uuid.uuid4()}"
    api_key = key or f"aq2_instantiate_contract_{uuid.uuid4().hex}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO actors (name, kind)
            VALUES (%s, %s)
            RETURNING id
            """,
            (actor_name, kind),
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
                f"instantiate-test-key-{uuid.uuid4()}",
                PASSWORD_HASHER.hash(api_key),
                api_key[:DISPLAY_PREFIX_LENGTH],
                lookup_id_for_key(api_key),
            ),
        )

    return actor_id, api_key


def _insert_project(
    conn: Connection[tuple[object, ...]],
    *,
    created_by_actor_id: UUID,
    slug: str | None = None,
    name: str = "Instantiate Test Project",
) -> UUID:
    project_slug = slug or f"{PROJECT_SLUG_PREFIX}{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO projects (name, slug, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (name, project_slug, created_by_actor_id),
        )
        row = cursor.fetchone()
    assert row is not None
    project_id = row[0]
    assert isinstance(project_id, UUID)
    return project_id


def _contract_profile_ids(
    conn: Connection[tuple[object, ...]],
    *names: str,
) -> dict[str, UUID]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, name
            FROM contract_profiles
            WHERE name = ANY(%s)
            """,
            (list(names),),
        )
        rows = cursor.fetchall()
    result = {row[1]: row[0] for row in rows}
    assert set(result) == set(names)
    return result


def _insert_workflow(
    conn: Connection[tuple[object, ...]],
    *,
    slug: str,
    name: str,
    version: int,
    created_by_actor_id: UUID,
    is_archived: bool = False,
    supersedes_workflow_id: UUID | None = None,
) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workflows
                (
                    slug,
                    name,
                    version,
                    is_archived,
                    created_by_actor_id,
                    supersedes_workflow_id
                )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                slug,
                name,
                version,
                is_archived,
                created_by_actor_id,
                supersedes_workflow_id,
            ),
        )
        row = cursor.fetchone()
    assert row is not None
    workflow_id = row[0]
    assert isinstance(workflow_id, UUID)
    return workflow_id


def _insert_workflow_step(
    conn: Connection[tuple[object, ...]],
    *,
    workflow_id: UUID,
    name: str,
    ordinal: int,
    default_contract_profile_id: UUID,
) -> UUID:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO workflow_steps
                (workflow_id, name, ordinal, default_contract_profile_id, step_edges)
            VALUES (%s, %s, %s, %s, '{}'::jsonb)
            RETURNING id
            """,
            (workflow_id, name, ordinal, default_contract_profile_id),
        )
        row = cursor.fetchone()
    assert row is not None
    step_id = row[0]
    assert isinstance(step_id, UUID)
    return step_id


def _auth_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _pipeline_row(
    conn: Connection[tuple[object, ...]],
    pipeline_id: UUID,
) -> dict[str, object]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, project_id, name, instantiated_from_workflow_id,
                   instantiated_from_workflow_version, created_by_actor_id
            FROM pipelines
            WHERE id = %s
            """,
            (pipeline_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return {
        "id": row[0],
        "project_id": row[1],
        "name": row[2],
        "instantiated_from_workflow_id": row[3],
        "instantiated_from_workflow_version": row[4],
        "created_by_actor_id": row[5],
    }


def _jobs_for_pipeline(
    conn: Connection[tuple[object, ...]],
    pipeline_id: UUID,
) -> list[dict[str, object]]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT jobs.id, jobs.pipeline_id, jobs.project_id, jobs.state, jobs.title,
                   jobs.contract_profile_id, jobs.instantiated_from_step_id,
                   jobs.created_by_actor_id
            FROM jobs
            LEFT JOIN workflow_steps
              ON workflow_steps.id = jobs.instantiated_from_step_id
            WHERE jobs.pipeline_id = %s
            ORDER BY workflow_steps.ordinal ASC NULLS LAST, jobs.id ASC
            """,
            (pipeline_id,),
        )
        rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "pipeline_id": row[1],
            "project_id": row[2],
            "state": row[3],
            "title": row[4],
            "contract_profile_id": row[5],
            "instantiated_from_step_id": row[6],
            "created_by_actor_id": row[7],
        }
        for row in rows
    ]


def _count_job_edges(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM job_edges")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _count_draft_jobs(conn: Connection[tuple[object, ...]]) -> int:
    with conn.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM jobs WHERE state = 'draft'")
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _count_pipelines_for_workflow(
    conn: Connection[tuple[object, ...]],
    workflow_id: UUID,
) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM pipelines
            WHERE instantiated_from_workflow_id = %s
            """,
            (workflow_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    return int(row[0])


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


@pytest.mark.asyncio
async def test_instantiate_pipeline_missing_bearer_returns_byte_equal_401(
    async_client: httpx.AsyncClient,
) -> None:
    response = await async_client.post(
        "/pipelines/from-workflow/ship-a-thing",
        json={
            "project_id": "11111111-1111-4111-8111-111111111111",
            "pipeline_name": "fix-the-thing",
        },
    )

    assert response.status_code == 401
    assert response.content == b'{"error":"unauthenticated"}'


@pytest.mark.asyncio
async def test_instantiate_pipeline_creates_ready_jobs_with_direct_step_fks_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="instantiate-test-founder")
    project_id = _insert_project(conn, created_by_actor_id=actor_id)
    profile_ids = _contract_profile_ids(
        conn,
        "research-decision",
        "coding-task",
        "bug-fix",
    )
    workflow_slug = "instantiate-test-ship-a-thing"
    workflow_id = _insert_workflow(
        conn,
        slug=workflow_slug,
        name="Ship A Thing",
        version=1,
        created_by_actor_id=actor_id,
    )
    step_ids = {
        "scope": _insert_workflow_step(
            conn,
            workflow_id=workflow_id,
            name="scope",
            ordinal=1,
            default_contract_profile_id=profile_ids["research-decision"],
        ),
        "build": _insert_workflow_step(
            conn,
            workflow_id=workflow_id,
            name="build",
            ordinal=2,
            default_contract_profile_id=profile_ids["coding-task"],
        ),
        "verify": _insert_workflow_step(
            conn,
            workflow_id=workflow_id,
            name="verify",
            ordinal=3,
            default_contract_profile_id=profile_ids["bug-fix"],
        ),
    }

    response = await async_client.post(
        f"/pipelines/from-workflow/{workflow_slug}",
        headers=_auth_headers(key),
        json={
            "project_id": str(project_id),
            "pipeline_name": "fix-the-thing",
        },
    )

    assert response.status_code == 200
    payload = InstantiatePipelineResponse.model_validate(response.json())
    assert payload.pipeline.project_id == project_id
    assert payload.pipeline.name == "fix-the-thing"
    assert payload.pipeline.instantiated_from_workflow_id == workflow_id
    assert payload.pipeline.instantiated_from_workflow_version == 1
    assert payload.pipeline.created_by_actor_id == actor_id
    assert [job.title for job in payload.jobs] == ["scope", "build", "verify"]
    assert all(job.pipeline_id == payload.pipeline.id for job in payload.jobs)
    assert all(job.project_id == project_id for job in payload.jobs)
    assert all(job.state == "ready" for job in payload.jobs)
    assert all(job.created_by_actor_id == actor_id for job in payload.jobs)
    assert {job.instantiated_from_step_id for job in payload.jobs} == set(
        step_ids.values()
    )

    jobs_by_step = {
        job.instantiated_from_step_id: job
        for job in payload.jobs
        if job.instantiated_from_step_id is not None
    }
    assert jobs_by_step[step_ids["scope"]].contract_profile_id == profile_ids[
        "research-decision"
    ]
    assert jobs_by_step[step_ids["build"]].contract_profile_id == profile_ids[
        "coding-task"
    ]
    assert jobs_by_step[step_ids["verify"]].contract_profile_id == profile_ids[
        "bug-fix"
    ]

    pipeline_row = _pipeline_row(conn, payload.pipeline.id)
    assert pipeline_row == {
        "id": payload.pipeline.id,
        "project_id": project_id,
        "name": "fix-the-thing",
        "instantiated_from_workflow_id": workflow_id,
        "instantiated_from_workflow_version": 1,
        "created_by_actor_id": actor_id,
    }
    db_jobs = _jobs_for_pipeline(conn, payload.pipeline.id)
    assert len(db_jobs) == 3
    assert [job["title"] for job in db_jobs] == ["scope", "build", "verify"]
    assert all(job["state"] == "ready" for job in db_jobs)
    assert all(job["project_id"] == project_id for job in db_jobs)
    assert _count_job_edges(conn) == 0
    assert _count_draft_jobs(conn) == 0
    assert _audit_rows(conn) == [
        {
            "op": "instantiate_pipeline",
            "target_kind": "pipeline",
            "target_id": str(payload.pipeline.id),
            "request_payload": {
                "workflow_slug": workflow_slug,
                "workflow_version": 1,
                "project_id": str(project_id),
                "pipeline_name": "fix-the-thing",
            },
            "response_payload": {
                "pipeline_id": str(payload.pipeline.id),
                "job_count": 3,
                "job_ids": [str(job.id) for job in payload.jobs],
            },
            "error_code": None,
        }
    ]


@pytest.mark.asyncio
async def test_instantiate_pipeline_pins_to_latest_non_archived_workflow_version(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="instantiate-test-founder")
    project_id = _insert_project(conn, created_by_actor_id=actor_id)
    profile_ids = _contract_profile_ids(conn, "coding-task", "bug-fix")
    workflow_slug = "instantiate-test-versioned"
    workflow_v1_id = _insert_workflow(
        conn,
        slug=workflow_slug,
        name="Release Train",
        version=1,
        created_by_actor_id=actor_id,
    )
    _insert_workflow_step(
        conn,
        workflow_id=workflow_v1_id,
        name="build",
        ordinal=1,
        default_contract_profile_id=profile_ids["coding-task"],
    )
    workflow_v2_id = _insert_workflow(
        conn,
        slug=workflow_slug,
        name="Release Train v2",
        version=2,
        created_by_actor_id=actor_id,
        supersedes_workflow_id=workflow_v1_id,
    )
    step_id = _insert_workflow_step(
        conn,
        workflow_id=workflow_v2_id,
        name="verify",
        ordinal=1,
        default_contract_profile_id=profile_ids["bug-fix"],
    )

    response = await async_client.post(
        f"/pipelines/from-workflow/{workflow_slug}",
        headers=_auth_headers(key),
        json={
            "project_id": str(project_id),
            "pipeline_name": "release-train-live",
        },
    )

    assert response.status_code == 200
    payload = InstantiatePipelineResponse.model_validate(response.json())
    assert payload.pipeline.instantiated_from_workflow_id == workflow_v2_id
    assert payload.pipeline.instantiated_from_workflow_version == 2
    assert [job.title for job in payload.jobs] == ["verify"]
    assert payload.jobs[0].instantiated_from_step_id == step_id
    assert payload.jobs[0].contract_profile_id == profile_ids["bug-fix"]


@pytest.mark.asyncio
async def test_instantiate_pipeline_missing_slug_returns_404_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="instantiate-test-founder")
    project_id = _insert_project(conn, created_by_actor_id=actor_id)

    response = await async_client.post(
        "/pipelines/from-workflow/instantiate-test-missing",
        headers=_auth_headers(key),
        json={
            "project_id": str(project_id),
            "pipeline_name": "missing-workflow",
        },
    )

    assert response.status_code == 404
    assert response.json() == {"error": "workflow_not_found"}
    assert _audit_rows(conn) == [
        {
            "op": "instantiate_pipeline",
            "target_kind": "pipeline",
            "target_id": None,
            "request_payload": {
                "workflow_slug": "instantiate-test-missing",
                "workflow_version": None,
                "project_id": str(project_id),
                "pipeline_name": "missing-workflow",
            },
            "response_payload": {"error": "workflow_not_found"},
            "error_code": "workflow_not_found",
        }
    ]


@pytest.mark.asyncio
async def test_instantiate_pipeline_archived_family_returns_409_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="instantiate-test-founder")
    project_id = _insert_project(conn, created_by_actor_id=actor_id)
    profile_ids = _contract_profile_ids(conn, "coding-task")
    workflow_slug = "instantiate-test-archived"
    workflow_v1_id = _insert_workflow(
        conn,
        slug=workflow_slug,
        name="Archived Flow",
        version=1,
        is_archived=True,
        created_by_actor_id=actor_id,
    )
    _insert_workflow_step(
        conn,
        workflow_id=workflow_v1_id,
        name="build",
        ordinal=1,
        default_contract_profile_id=profile_ids["coding-task"],
    )
    workflow_v2_id = _insert_workflow(
        conn,
        slug=workflow_slug,
        name="Archived Flow v2",
        version=2,
        is_archived=True,
        created_by_actor_id=actor_id,
        supersedes_workflow_id=workflow_v1_id,
    )
    _insert_workflow_step(
        conn,
        workflow_id=workflow_v2_id,
        name="verify",
        ordinal=1,
        default_contract_profile_id=profile_ids["coding-task"],
    )

    response = await async_client.post(
        f"/pipelines/from-workflow/{workflow_slug}",
        headers=_auth_headers(key),
        json={
            "project_id": str(project_id),
            "pipeline_name": "cannot-instantiate",
        },
    )

    assert response.status_code == 409
    assert response.json() == {"error": "workflow_archived"}
    assert _audit_rows(conn) == [
        {
            "op": "instantiate_pipeline",
            "target_kind": "pipeline",
            "target_id": None,
            "request_payload": {
                "workflow_slug": workflow_slug,
                "workflow_version": 2,
                "project_id": str(project_id),
                "pipeline_name": "cannot-instantiate",
            },
            "response_payload": {"error": "workflow_archived"},
            "error_code": "workflow_archived",
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_body", "missing_field"),
    [
        ({"pipeline_name": "missing-project"}, "project_id"),
        (
            {"project_id": "11111111-1111-4111-8111-111111111111"},
            "pipeline_name",
        ),
    ],
)
async def test_instantiate_pipeline_missing_required_fields_returns_422(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    request_body: dict[str, str],
    missing_field: str,
) -> None:
    _actor_id, key = _insert_actor_with_key(conn, name="instantiate-test-founder")

    response = await async_client.post(
        "/pipelines/from-workflow/instantiate-test-validation",
        headers=_auth_headers(key),
        json=request_body,
    )

    assert response.status_code == 422
    assert missing_field in response.text
    assert _audit_rows(conn) == []


@pytest.mark.asyncio
async def test_concurrent_instantiate_pipeline_requests_both_succeed(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = _insert_actor_with_key(conn, name="instantiate-test-founder")
    project_id = _insert_project(conn, created_by_actor_id=actor_id)
    profile_ids = _contract_profile_ids(conn, "research-decision", "coding-task")
    workflow_slug = "instantiate-test-concurrency"
    workflow_id = _insert_workflow(
        conn,
        slug=workflow_slug,
        name="Concurrent Flow",
        version=1,
        created_by_actor_id=actor_id,
    )
    _insert_workflow_step(
        conn,
        workflow_id=workflow_id,
        name="scope",
        ordinal=1,
        default_contract_profile_id=profile_ids["research-decision"],
    )
    _insert_workflow_step(
        conn,
        workflow_id=workflow_id,
        name="build",
        ordinal=2,
        default_contract_profile_id=profile_ids["coding-task"],
    )

    async def instantiate(name: str) -> httpx.Response:
        return await async_client.post(
            f"/pipelines/from-workflow/{workflow_slug}",
            headers=_auth_headers(key),
            json={
                "project_id": str(project_id),
                "pipeline_name": name,
            },
        )

    left, right = await asyncio.gather(
        instantiate("burst-a"),
        instantiate("burst-b"),
    )

    assert left.status_code == 200
    assert right.status_code == 200
    left_payload = InstantiatePipelineResponse.model_validate(left.json())
    right_payload = InstantiatePipelineResponse.model_validate(right.json())
    assert left_payload.pipeline.id != right_payload.pipeline.id
    assert len(left_payload.jobs) == 2
    assert len(right_payload.jobs) == 2
    assert _count_pipelines_for_workflow(conn, workflow_id) == 2
    assert len(_audit_rows(conn)) == 2


def test_jobs_composite_fk_rejects_project_mismatch(
    conn: Connection[tuple[object, ...]],
) -> None:
    actor_id, _key = _insert_actor_with_key(conn, name="instantiate-test-founder")
    project_a = _insert_project(
        conn,
        created_by_actor_id=actor_id,
        slug="instantiate-test-project-a",
    )
    project_b = _insert_project(
        conn,
        created_by_actor_id=actor_id,
        slug="instantiate-test-project-b",
    )
    profile_id = _contract_profile_ids(conn, "coding-task")["coding-task"]
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO pipelines (project_id, name, created_by_actor_id)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (project_a, "fk-guard-pipeline", actor_id),
        )
        row = cursor.fetchone()
    assert row is not None
    pipeline_id = row[0]

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO jobs
                    (
                        pipeline_id,
                        project_id,
                        state,
                        title,
                        contract_profile_id,
                        created_by_actor_id
                    )
                VALUES (%s, %s, 'ready', %s, %s, %s)
                """,
                (
                    pipeline_id,
                    project_b,
                    "fk-mismatch-job",
                    profile_id,
                    actor_id,
                ),
            )
