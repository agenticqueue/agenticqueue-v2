import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

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
from aq_api.models import ResetClaimResponse
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live reset_claim tests",
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


def _mark_claimed(
    conn: Connection[tuple[object, ...]],
    job_id: UUID,
    *,
    actor_id: UUID,
    state: str = "in_progress",
) -> None:
    claimed_at = datetime.now(UTC)
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE jobs
            SET state = %s,
                claimed_by_actor_id = %s,
                claimed_at = %s,
                claim_heartbeat_at = %s
            WHERE id = %s
            """,
            (state, actor_id, claimed_at, claimed_at, job_id),
        )


def _job_fixture(
    conn: Connection[tuple[object, ...]],
) -> tuple[UUID, str, UUID]:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="reset target",
    )
    _mark_claimed(conn, job_id, actor_id=actor_id)
    return actor_id, key, job_id


@pytest.mark.asyncio
async def test_reset_claim_any_actor_returns_ready_and_audits_reason(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, _key, job_id = _job_fixture(conn)
    _reset_actor_id, reset_key = insert_actor_with_key(conn, name="job-test-resetter")
    reason = "claimant crashed"

    response = await async_client.post(
        f"/jobs/{job_id}/reset-claim",
        headers=auth_headers(reset_key),
        json={"reason": reason},
    )

    assert response.status_code == 200
    payload = ResetClaimResponse.model_validate(response.json())
    assert payload.job.id == job_id
    assert payload.job.state == "ready"
    assert payload.job.claimed_by_actor_id is None
    assert payload.job.claimed_at is None
    assert payload.job.claim_heartbeat_at is None

    stored = job_row(conn, job_id)
    assert stored["state"] == "ready"
    assert stored["claimed_by_actor_id"] is None
    assert stored["claimed_at"] is None
    assert stored["claim_heartbeat_at"] is None

    assert audit_rows(conn) == [
        {
            "op": "reset_claim",
            "target_kind": "job",
            "target_id": str(job_id),
            "request_payload": {"job_id": str(job_id), "reason": reason},
            "response_payload": response.json(),
            "error_code": None,
        }
    ]


@pytest.mark.asyncio
async def test_reset_claim_not_claimed_returns_409_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="ready reset target",
    )

    response = await async_client.post(
        f"/jobs/{job_id}/reset-claim",
        headers=auth_headers(key),
        json={"reason": "operator reset"},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "job_not_claimed"}
    assert audit_rows(conn) == [
        {
            "op": "reset_claim",
            "target_kind": "job",
            "target_id": str(job_id),
            "request_payload": {
                "job_id": str(job_id),
                "reason": "operator reset",
            },
            "response_payload": {"error": "job_not_claimed"},
            "error_code": "job_not_claimed",
        }
    ]


@pytest.mark.asyncio
async def test_reset_claim_empty_or_missing_reason_returns_422_without_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, job_id = _job_fixture(conn)

    for payload in ({}, {"reason": ""}):
        response = await async_client.post(
            f"/jobs/{job_id}/reset-claim",
            headers=auth_headers(key),
            json=payload,
        )
        assert response.status_code == 422

    assert audit_rows(conn) == []


@pytest.mark.asyncio
async def test_reset_claim_missing_job_returns_404_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id = _fixture_project(conn)
    missing_job_id = uuid4()

    response = await async_client.post(
        f"/jobs/{missing_job_id}/reset-claim",
        headers=auth_headers(key),
        json={"reason": "operator reset"},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "job_not_found"}
    assert audit_rows(conn) == [
        {
            "op": "reset_claim",
            "target_kind": "job",
            "target_id": str(missing_job_id),
            "request_payload": {
                "job_id": str(missing_job_id),
                "reason": "operator reset",
            },
            "response_payload": {"error": "job_not_found"},
            "error_code": "job_not_found",
        }
    ]
