import os
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import httpx
import psycopg
import pytest
import pytest_asyncio
from _jobs_test_support import (
    audit_rows,
    auth_headers,
    insert_actor_with_key,
    insert_job,
    insert_pipeline,
    insert_project,
    truncate_job_state,
)
from aq_api.app import app
from aq_api.models import ListReadyJobsResponse
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live list_ready_jobs tests",
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


def _fixture_project(
    conn: Connection[tuple[object, ...]],
) -> tuple[UUID, str, UUID, UUID]:
    actor_id, key = insert_actor_with_key(conn, name="job-test-founder")
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
    )
    return actor_id, key, project_id, pipeline_id


@pytest.mark.asyncio
async def test_list_ready_jobs_preserves_fifo_within_project_label_scope(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    other_project_id = insert_project(conn, created_by_actor_id=actor_id)
    other_pipeline_id = insert_pipeline(
        conn,
        project_id=other_project_id,
        created_by_actor_id=actor_id,
    )
    archived_pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
        archived=True,
    )
    first = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="first web fix",
        labels=["area:web", "kind:fix"],
        created_at_offset_seconds=-50,
    )
    second = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="second web",
        labels=["area:web"],
        created_at_offset_seconds=-40,
    )
    insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="api only",
        labels=["area:api"],
        created_at_offset_seconds=-30,
    )
    insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="in progress web",
        state="in_progress",
        labels=["area:web"],
        created_at_offset_seconds=-20,
    )
    insert_job(
        conn,
        pipeline_id=archived_pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="archived pipeline web",
        labels=["area:web"],
        created_at_offset_seconds=-10,
    )
    insert_job(
        conn,
        pipeline_id=other_pipeline_id,
        project_id=other_project_id,
        created_by_actor_id=actor_id,
        title="other project web",
        labels=["area:web"],
        created_at_offset_seconds=0,
    )

    response = await async_client.get(
        "/jobs/ready",
        headers=auth_headers(key),
        params={"project": str(project_id), "label": "area:web", "limit": 50},
    )
    both_label_response = await async_client.get(
        "/jobs/ready",
        headers=auth_headers(key),
        params=[
            ("project", str(project_id)),
            ("label", "area:web"),
            ("label", "kind:fix"),
        ],
    )

    assert response.status_code == 200
    payload = ListReadyJobsResponse.model_validate(response.json())
    assert [job.id for job in payload.jobs] == [first, second]
    assert all(job.project_id == project_id for job in payload.jobs)
    assert all(job.state == "ready" for job in payload.jobs)
    assert payload.next_cursor is None

    assert both_label_response.status_code == 200
    both_label_payload = ListReadyJobsResponse.model_validate(
        both_label_response.json()
    )
    assert [job.id for job in both_label_payload.jobs] == [first]


@pytest.mark.asyncio
async def test_list_ready_jobs_never_audits_repeated_reads(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="read-only ready",
        labels=["area:web"],
    )
    assert audit_rows(conn) == []

    for _ in range(100):
        response = await async_client.get(
            "/jobs/ready",
            headers=auth_headers(key),
            params={"project": str(project_id), "label": "area:web"},
        )
        assert response.status_code == 200

    assert audit_rows(conn) == []


@pytest.mark.asyncio
async def test_list_ready_jobs_cursor_stays_stable_across_new_inserts(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    first = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="first",
        labels=["area:web"],
        created_at_offset_seconds=-30,
    )
    second = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="second",
        labels=["area:web"],
        created_at_offset_seconds=-20,
    )
    third = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="third",
        labels=["area:web"],
        created_at_offset_seconds=-10,
    )

    first_page_response = await async_client.get(
        "/jobs/ready",
        headers=auth_headers(key),
        params={"project": str(project_id), "label": "area:web", "limit": 2},
    )
    assert first_page_response.status_code == 200
    first_page = ListReadyJobsResponse.model_validate(first_page_response.json())
    assert [job.id for job in first_page.jobs] == [first, second]
    assert first_page.next_cursor is not None

    fourth = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="fourth",
        labels=["area:web"],
        created_at_offset_seconds=10,
    )
    second_page_response = await async_client.get(
        "/jobs/ready",
        headers=auth_headers(key),
        params={
            "project": str(project_id),
            "label": "area:web",
            "limit": 2,
            "cursor": first_page.next_cursor,
        },
    )

    assert second_page_response.status_code == 200
    second_page = ListReadyJobsResponse.model_validate(second_page_response.json())
    assert [job.id for job in second_page.jobs] == [third, fourth]
    assert {job.id for job in first_page.jobs}.isdisjoint(
        {job.id for job in second_page.jobs}
    )


@pytest.mark.asyncio
async def test_list_ready_jobs_project_required_tampered_cursor_and_limit_clamp(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    for index in range(101):
        insert_job(
            conn,
            pipeline_id=pipeline_id,
            project_id=project_id,
            created_by_actor_id=actor_id,
            title=f"ready {index}",
            labels=["area:web"],
            created_at_offset_seconds=index,
        )

    missing_project_response = await async_client.get(
        "/jobs/ready",
        headers=auth_headers(key),
    )
    tampered_cursor_response = await async_client.get(
        "/jobs/ready",
        headers=auth_headers(key),
        params={"project": str(project_id), "cursor": "not-a-valid-cursor"},
    )
    oversized_limit_response = await async_client.get(
        "/jobs/ready",
        headers=auth_headers(key),
        params={"project": str(project_id), "limit": 999},
    )

    assert missing_project_response.status_code == 422
    assert tampered_cursor_response.status_code == 422
    assert tampered_cursor_response.json() == {"error": "invalid_cursor"}
    assert oversized_limit_response.status_code == 200
    payload = ListReadyJobsResponse.model_validate(oversized_limit_response.json())
    assert len(payload.jobs) == 100
    assert payload.next_cursor is not None


@pytest.mark.asyncio
async def test_list_ready_jobs_excludes_template_pipeline_jobs(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    template_pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
        name="ship-a-thing",
        is_template=True,
    )
    live_job = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="live ready",
        labels=["area:web"],
        created_at_offset_seconds=-10,
    )
    template_jobs = {
        insert_job(
            conn,
            pipeline_id=template_pipeline_id,
            project_id=project_id,
            created_by_actor_id=actor_id,
            title=f"template ready {index}",
            labels=["area:web"],
            created_at_offset_seconds=index,
        )
        for index in range(3)
    }

    response = await async_client.get(
        "/jobs/ready",
        headers=auth_headers(key),
        params={"project": str(project_id), "label": "area:web"},
    )

    assert response.status_code == 200
    payload = ListReadyJobsResponse.model_validate(response.json())
    result_ids = {job.id for job in payload.jobs}
    assert result_ids == {live_job}
    assert result_ids.isdisjoint(template_jobs)
