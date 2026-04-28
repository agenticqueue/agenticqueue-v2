import os
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

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
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live job rejection tests",
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


def _fixture_job(conn: Connection[tuple[object, ...]]) -> tuple[UUID, str, UUID]:
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
        title="immutable job",
    )
    return job_id, key, project_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        ({"state": "done"}, "cannot_write_state_via_update"),
        ({"labels": ["area:web"]}, "cannot_write_labels_via_update"),
        (
            {"contract": {"contract_type": "changed"}},
            "cannot_write_contract_via_update",
        ),
        (
            {"claimed_by_actor_id": "11111111-1111-4111-8111-111111111111"},
            "cannot_write_claim_via_update",
        ),
    ],
)
async def test_update_job_rejects_forbidden_fields_with_400_and_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    payload: dict[str, object],
    error_code: str,
) -> None:
    job_id, key, _project_id = _fixture_job(conn)

    response = await async_client.patch(
        f"/jobs/{job_id}",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 400
    assert response.json() == {"error": error_code}
    stored = job_row(conn, job_id)
    assert stored["state"] == "ready"
    assert stored["title"] == "immutable job"
    assert stored["contract"] == CONTRACT
    assert stored["labels"] == []
    assert stored["claimed_by_actor_id"] is None

    rows = audit_rows(conn)
    assert rows == [
        {
            "op": "update_job",
            "target_kind": "job",
            "target_id": str(job_id),
            "request_payload": {"job_id": str(job_id), **payload},
            "response_payload": {"error": error_code},
            "error_code": error_code,
        }
    ]
