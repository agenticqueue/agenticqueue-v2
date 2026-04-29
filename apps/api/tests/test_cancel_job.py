import os
from collections.abc import AsyncIterator, Iterator

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
    job_row,
    truncate_job_state,
)
from aq_api.app import app
from aq_api.models import CancelJobResponse
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live job cancel tests",
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


def _fixture_job(
    conn: Connection[tuple[object, ...]],
    *,
    state: str,
) -> tuple[str, str]:
    actor_id, key = insert_actor_with_key(conn, name="job-test-founder")
    project_id = insert_project(conn, created_by_actor_id=actor_id)
    pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
    )
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title=f"{state} job",
        state=state,
    )
    return key, str(job_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["ready", "in_progress", "blocked", "pending_review"])
async def test_cancel_job_transitions_non_terminal_states_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    state: str,
) -> None:
    key, job_id = _fixture_job(conn, state=state)

    response = await async_client.post(
        f"/jobs/{job_id}/cancel",
        headers=auth_headers(key),
    )

    assert response.status_code == 200
    payload = CancelJobResponse.model_validate(response.json())
    assert str(payload.job.id) == job_id
    assert payload.job.state == "cancelled"
    assert job_row(conn, payload.job.id)["state"] == "cancelled"
    assert audit_rows(conn) == [
        {
            "op": "cancel_job",
            "target_kind": "job",
            "target_id": job_id,
            "request_payload": {"job_id": job_id},
            "response_payload": response.json(),
            "error_code": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["done", "failed", "cancelled"])
async def test_cancel_job_terminal_states_return_409_and_audit_current_state(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    state: str,
) -> None:
    key, job_id = _fixture_job(conn, state=state)

    response = await async_client.post(
        f"/jobs/{job_id}/cancel",
        headers=auth_headers(key),
    )

    assert response.status_code == 409
    assert response.json() == {"error": "already_terminal"}
    assert job_row(conn, job_id)["state"] == state
    assert audit_rows(conn) == [
        {
            "op": "cancel_job",
            "target_kind": "job",
            "target_id": job_id,
            "request_payload": {"job_id": job_id},
            "response_payload": {"error": "already_terminal", "state": state},
            "error_code": "already_terminal",
        }
    ]
