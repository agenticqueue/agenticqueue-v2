import json
import os
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
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
    job_row,
    truncate_job_state,
)
from aq_api._datetime import parse_utc
from aq_api._request_context import (
    reset_authenticated_actor_id,
    set_authenticated_actor_id,
)
from aq_api.app import app
from aq_api.mcp import create_mcp_server
from aq_api.models import ClaimNextJobResponse
from fastmcp import Client
from psycopg import Connection

DATABASE_URL_SYNC = os.environ.get("DATABASE_URL_SYNC")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL_SYNC,
    reason="DATABASE_URL_SYNC is required for live claim_next_job tests",
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


async def _mcp_call(
    actor_id: UUID,
    tool: str,
    arguments: dict[str, object],
) -> dict[str, object]:
    context_token = set_authenticated_actor_id(actor_id)
    try:
        async with Client(create_mcp_server()) as client:
            result = await client.call_tool(tool, arguments)
    finally:
        reset_authenticated_actor_id(context_token)
    assert result.structured_content is not None
    return {
        "structuredContent": result.structured_content,
        "content": [block.model_dump(mode="json") for block in result.content],
    }


@pytest.mark.asyncio
async def test_claim_next_job_claims_ready_job_and_audits_success(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="claimable ready job",
        labels=["area:web"],
    )

    response = await async_client.post(
        "/jobs/claim",
        headers=auth_headers(key),
        json={"project_id": str(project_id)},
    )

    assert response.status_code == 200
    payload = ClaimNextJobResponse.model_validate(response.json())
    assert payload.job.id == job_id
    assert payload.job.state == "in_progress"
    assert payload.job.claimed_by_actor_id == actor_id
    assert payload.job.claimed_at is not None
    assert payload.job.claim_heartbeat_at == payload.job.claimed_at
    assert payload.packet.project_id == project_id
    assert payload.packet.pipeline_id == pipeline_id
    assert payload.packet.current_job_id == job_id
    assert payload.packet.previous_jobs == []
    assert payload.packet.next_job_id is None
    assert payload.lease_seconds == 900
    assert payload.recommended_heartbeat_after_seconds == 30
    assert payload.lease_expires_at == payload.job.claimed_at + timedelta(seconds=900)

    stored = job_row(conn, job_id)
    assert stored["state"] == "in_progress"
    assert stored["claimed_by_actor_id"] == actor_id
    assert stored["claimed_at"] is not None
    assert stored["claim_heartbeat_at"] == stored["claimed_at"]

    rows = audit_rows(conn)
    assert rows == [
        {
            "op": "claim_next_job",
            "target_kind": "job",
            "target_id": str(job_id),
            "request_payload": {
                "project_id": str(project_id),
                "label_filter": [],
            },
            "response_payload": response.json(),
            "error_code": None,
        }
    ]


@pytest.mark.asyncio
async def test_claim_next_job_label_filter_preserves_fifo_within_label_scope(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="older web",
        labels=["area:web"],
        created_at_offset_seconds=-50,
    )
    first_api = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="first api",
        labels=["area:api"],
        created_at_offset_seconds=-40,
    )
    insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="second api",
        labels=["area:api"],
        created_at_offset_seconds=-30,
    )
    insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="newer web",
        labels=["area:web"],
        created_at_offset_seconds=-20,
    )

    response = await async_client.post(
        "/jobs/claim",
        headers=auth_headers(key),
        json={"project_id": str(project_id), "label_filter": ["area:api"]},
    )

    assert response.status_code == 200
    payload = ClaimNextJobResponse.model_validate(response.json())
    assert payload.job.id == first_api
    assert payload.job.labels == ["area:api"]
    rows = audit_rows(conn)
    assert rows[0]["request_payload"] == {
        "project_id": str(project_id),
        "label_filter": ["area:api"],
    }


@pytest.mark.asyncio
async def test_claim_next_job_excludes_cross_project_template_and_archived_jobs(
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
    template_pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
        name="ship-a-thing",
        is_template=True,
    )
    archived_pipeline_id = insert_pipeline(
        conn,
        project_id=project_id,
        created_by_actor_id=actor_id,
        archived=True,
    )
    insert_job(
        conn,
        pipeline_id=other_pipeline_id,
        project_id=other_project_id,
        created_by_actor_id=actor_id,
        title="old other project",
        created_at_offset_seconds=-60,
    )
    template_jobs = [
        insert_job(
            conn,
            pipeline_id=template_pipeline_id,
            project_id=project_id,
            created_by_actor_id=actor_id,
            title=f"template ready {index}",
            labels=["area:web"],
            created_at_offset_seconds=-50 + index,
        )
        for index in range(3)
    ]
    archived_job = insert_job(
        conn,
        pipeline_id=archived_pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="archived pipeline ready",
        created_at_offset_seconds=-20,
    )
    live_job = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="live ready",
        created_at_offset_seconds=-10,
    )

    for job_id in [*template_jobs, archived_job, live_job]:
        assert job_row(conn, job_id)["state"] == "ready"

    response = await async_client.post(
        "/jobs/claim",
        headers=auth_headers(key),
        json={"project_id": str(project_id)},
    )

    assert response.status_code == 200
    payload = ClaimNextJobResponse.model_validate(response.json())
    assert payload.job.id == live_job
    for job_id in [*template_jobs, archived_job]:
        assert job_row(conn, job_id)["state"] == "ready"
        assert job_row(conn, job_id)["claimed_by_actor_id"] is None


@pytest.mark.asyncio
async def test_claim_next_job_no_ready_job_returns_409_and_audits_denial(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, project_id, _pipeline_id = _fixture_project(conn)

    response = await async_client.post(
        "/jobs/claim",
        headers=auth_headers(key),
        json={"project_id": str(project_id), "label_filter": ["area:api"]},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "no_ready_job"}
    assert audit_rows(conn) == [
        {
            "op": "claim_next_job",
            "target_kind": "job",
            "target_id": None,
            "request_payload": {
                "project_id": str(project_id),
                "label_filter": ["area:api"],
            },
            "response_payload": {"error": "no_ready_job"},
            "error_code": "no_ready_job",
        }
    ]


@pytest.mark.asyncio
async def test_claim_next_job_invalid_input_returns_422_without_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, project_id, _pipeline_id = _fixture_project(conn)
    invalid_payloads = [
        {},
        {"project_id": "not-a-uuid"},
        {"project_id": str(project_id), "label_filter": ["bad label"]},
        {"project_id": str(project_id), "label_filter": [""]},
        {"project_id": str(project_id), "label_filter": [None]},
    ]

    for payload in invalid_payloads:
        response = await async_client.post(
            "/jobs/claim",
            headers=auth_headers(key),
            json=payload,
        )
        assert response.status_code == 422

    assert audit_rows(conn) == []


@pytest.mark.asyncio
async def test_claim_next_job_mcp_returns_multipart_and_structured_payload(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, _key, project_id, pipeline_id = _fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="mcp claimable",
    )

    result = await _mcp_call(
        actor_id,
        "claim_next_job",
        {
            "project_id": str(project_id),
            "agent_identity": "claim-test-mcp",
        },
    )

    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    payload = ClaimNextJobResponse.model_validate(structured)
    assert payload.job.id == job_id
    assert payload.packet.current_job_id == job_id
    assert payload.packet.previous_jobs == []
    assert payload.packet.next_job_id is None

    content = result["content"]
    assert isinstance(content, list)
    assert len(content) == 3
    first_block = json.loads(content[0]["text"])
    second_block = json.loads(content[1]["text"])
    assert first_block == {"job": structured["job"]}
    assert second_block == {"packet": structured["packet"]}
    assert "heartbeat_job" in content[2]["text"]
    assert "submit_job ships in cap #5" in content[2]["text"]


@pytest.mark.asyncio
async def test_claim_next_job_lease_fields_honor_settings(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aq_api._settings import settings

    actor_id, key, project_id, pipeline_id = _fixture_project(conn)
    insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        title="lease check",
    )
    monkeypatch.setattr(settings, "claim_lease_seconds", 120)

    response = await async_client.post(
        "/jobs/claim",
        headers=auth_headers(key),
        json={"project_id": str(project_id)},
    )

    assert response.status_code == 200
    payload = response.json()
    claimed_at = parse_utc(payload["job"]["claimed_at"])
    lease_expires_at = parse_utc(payload["lease_expires_at"])
    assert payload["lease_seconds"] == 120
    assert payload["recommended_heartbeat_after_seconds"] == 30
    assert lease_expires_at == claimed_at + timedelta(seconds=120)
