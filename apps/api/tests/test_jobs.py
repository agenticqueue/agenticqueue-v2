import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC

import httpx
import psycopg
import pytest
import pytest_asyncio
from _jobs_test_support import (
    CONTRACT,
    audit_rows,
    auth_headers,
    insert_actor_with_key,
    insert_job,
    insert_pipeline,
    insert_project,
    job_row,
    truncate_job_state,
)
from aq_api.app import app
from aq_api.models import (
    CreateJobResponse,
    GetJobResponse,
    ListJobsResponse,
    UpdateJobResponse,
)
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live job tests",
)


@pytest.fixture()
def conn() -> Iterator[Connection[tuple[object, ...]]]:
    assert DATABASE_URL_SYNC is not None
    conninfo = DATABASE_URL_SYNC.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(conninfo, autocommit=True) as connection:
        truncate_job_state(connection)
        yield connection
        truncate_job_state(connection)


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


@pytest.mark.asyncio
async def test_job_routes_missing_bearer_returns_byte_equal_401(
    async_client: httpx.AsyncClient,
) -> None:
    expected = b'{"error":"unauthenticated"}'
    job_id = "77777777-7777-4777-8777-777777777777"
    pipeline_id = "66666666-6666-4666-8666-666666666666"

    responses = [
        await async_client.post(
            "/jobs",
            json={
                "pipeline_id": pipeline_id,
                "title": "No Auth",
                "contract": CONTRACT,
            },
        ),
        await async_client.get("/jobs"),
        await async_client.get(f"/jobs/{job_id}"),
        await async_client.patch(f"/jobs/{job_id}", json={"title": "No Auth"}),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response.content == expected


@pytest.mark.asyncio
async def test_job_rest_ops_create_list_get_update_and_forward_compat(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = insert_actor_with_key(conn, name="job-test-founder")
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
        name="release-pipeline",
    )
    headers = auth_headers(key)

    create_response = await async_client.post(
        "/jobs",
        headers=headers,
        json={
            "pipeline_id": str(pipeline_id),
            "title": "Build the thing",
            "description": "Implement the scoped change",
            "contract": CONTRACT,
        },
    )
    assert create_response.status_code == 200
    created = CreateJobResponse.model_validate(create_response.json())
    assert created.job.pipeline_id == pipeline_id
    assert created.job.project_id == project_id
    assert created.job.state == "ready"
    assert created.job.title == "Build the thing"
    assert created.job.description == "Implement the scoped change"
    assert created.job.contract == CONTRACT
    assert created.job.labels == []
    assert created.job.created_by_actor_id == actor_id
    assert created.job.created_at.tzinfo == UTC

    list_response = await async_client.get(
        "/jobs",
        headers=headers,
        params={"project_id": str(project_id), "limit": 100},
    )
    assert list_response.status_code == 200
    listed = ListJobsResponse.model_validate(list_response.json())
    assert created.job.id in {job.id for job in listed.jobs}

    get_response = await async_client.get(
        f"/jobs/{created.job.id}",
        headers=headers,
    )
    assert get_response.status_code == 200
    get_payload = get_response.json()
    assert get_payload["decisions"] == {"direct": [], "inherited": []}
    assert get_payload["learnings"] == {"direct": [], "inherited": []}
    fetched = GetJobResponse.model_validate(get_payload)
    assert fetched.job == created.job

    update_response = await async_client.patch(
        f"/jobs/{created.job.id}",
        headers=headers,
        json={"title": "Build the thing v2", "description": None},
    )
    assert update_response.status_code == 200
    updated = UpdateJobResponse.model_validate(update_response.json())
    assert updated.job.id == created.job.id
    assert updated.job.title == "Build the thing v2"
    assert updated.job.description is None
    assert updated.job.state == "ready"
    assert updated.job.contract == CONTRACT

    stored = job_row(conn, created.job.id)
    assert stored["project_id"] == project_id
    assert stored["state"] == "ready"
    assert stored["contract"] == CONTRACT

    rows = audit_rows(conn)
    assert [row["op"] for row in rows] == ["create_job", "update_job"]
    assert rows[0]["target_kind"] == "job"
    assert rows[0]["target_id"] == str(created.job.id)
    assert rows[0]["request_payload"] == {
        "pipeline_id": str(pipeline_id),
        "title": "Build the thing",
        "description": "Implement the scoped change",
        "contract": CONTRACT,
    }
    assert rows[0]["error_code"] is None
    assert rows[1]["target_id"] == str(created.job.id)
    assert rows[1]["request_payload"] == {
        "job_id": str(created.job.id),
        "title": "Build the thing v2",
        "description": None,
    }
    assert rows[1]["error_code"] is None


@pytest.mark.asyncio
async def test_create_job_requires_non_null_contract(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = insert_actor_with_key(conn, name="job-test-founder")
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
    )
    headers = auth_headers(key)

    missing_response = await async_client.post(
        "/jobs",
        headers=headers,
        json={"pipeline_id": str(pipeline_id), "title": "Missing contract"},
    )
    null_response = await async_client.post(
        "/jobs",
        headers=headers,
        json={
            "pipeline_id": str(pipeline_id),
            "title": "Null contract",
            "contract": None,
        },
    )

    assert missing_response.status_code == 422
    assert null_response.status_code == 422
    assert audit_rows(conn) == []


@pytest.mark.asyncio
async def test_create_job_pipeline_not_found_returns_404_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key = insert_actor_with_key(conn, name="job-test-founder")
    missing_pipeline = uuid.UUID("88888888-8888-4888-8888-888888888888")

    create_response = await async_client.post(
        "/jobs",
        headers=auth_headers(key),
        json={
            "pipeline_id": str(missing_pipeline),
            "title": "Missing pipeline",
            "contract": CONTRACT,
        },
    )

    assert create_response.status_code == 404
    assert create_response.json() == {"error": "pipeline_not_found"}
    assert audit_rows(conn) == [
        {
            "op": "create_job",
            "target_kind": "job",
            "target_id": None,
            "request_payload": {
                "pipeline_id": str(missing_pipeline),
                "title": "Missing pipeline",
                "contract": CONTRACT,
            },
            "response_payload": {"error": "pipeline_not_found"},
            "error_code": "pipeline_not_found",
        }
    ]


@pytest.mark.asyncio
async def test_list_jobs_filters_and_paginates(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key = insert_actor_with_key(conn, name="job-test-founder")
    project_a = insert_project(conn, created_by_actor_id=actor_id)
    project_b = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_a = insert_pipeline(
        conn,
        project_id=project_a,
        created_by_actor_id=actor_id,
        name="pipeline-a",
    )
    pipeline_b = insert_pipeline(
        conn,
        project_id=project_a,
        created_by_actor_id=actor_id,
        name="pipeline-b",
    )
    pipeline_c = insert_pipeline(
        conn,
        project_id=project_b,
        created_by_actor_id=actor_id,
        name="pipeline-c",
    )
    ready_a = insert_job(
        conn,
        pipeline_id=pipeline_a,
        project_id=project_a,
        created_by_actor_id=actor_id,
        title="ready-a",
    )
    done_a = insert_job(
        conn,
        pipeline_id=pipeline_a,
        project_id=project_a,
        created_by_actor_id=actor_id,
        title="done-a",
        state="done",
    )
    ready_b = insert_job(
        conn,
        pipeline_id=pipeline_b,
        project_id=project_a,
        created_by_actor_id=actor_id,
        title="ready-b",
    )
    other_project = insert_job(
        conn,
        pipeline_id=pipeline_c,
        project_id=project_b,
        created_by_actor_id=actor_id,
        title="other-project",
    )
    headers = auth_headers(key)

    project_response = await async_client.get(
        "/jobs",
        headers=headers,
        params={"project_id": str(project_a), "limit": 100},
    )
    pipeline_response = await async_client.get(
        "/jobs",
        headers=headers,
        params={"pipeline_id": str(pipeline_a), "limit": 100},
    )
    ready_response = await async_client.get(
        "/jobs",
        headers=headers,
        params={"project_id": str(project_a), "state": "ready", "limit": 100},
    )

    assert project_response.status_code == 200
    assert pipeline_response.status_code == 200
    assert ready_response.status_code == 200
    project_page = ListJobsResponse.model_validate(project_response.json())
    pipeline_page = ListJobsResponse.model_validate(pipeline_response.json())
    ready_page = ListJobsResponse.model_validate(ready_response.json())
    assert {job.id for job in project_page.jobs} == {ready_a, done_a, ready_b}
    assert {job.id for job in pipeline_page.jobs} == {ready_a, done_a}
    assert {job.id for job in ready_page.jobs} == {ready_a, ready_b}
    assert other_project not in {job.id for job in project_page.jobs}

    first_page_response = await async_client.get(
        "/jobs",
        headers=headers,
        params={"project_id": str(project_a), "limit": 2},
    )
    assert first_page_response.status_code == 200
    first_page = ListJobsResponse.model_validate(first_page_response.json())
    assert len(first_page.jobs) == 2
    assert first_page.next_cursor is not None

    second_page_response = await async_client.get(
        "/jobs",
        headers=headers,
        params={
            "project_id": str(project_a),
            "limit": 2,
            "cursor": first_page.next_cursor,
        },
    )
    assert second_page_response.status_code == 200
    second_page = ListJobsResponse.model_validate(second_page_response.json())
    assert len(second_page.jobs) == 1
    assert second_page.next_cursor is None
    assert {job.id for job in first_page.jobs}.isdisjoint(
        {job.id for job in second_page.jobs}
    )
