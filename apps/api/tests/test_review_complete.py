from __future__ import annotations

from typing import Any

import httpx
import pytest
from _submit_job_test_support import (
    CONTRACT,
    DB_SKIP,
    audit_rows,
    auth_headers,
    fixture_project,
    insert_actor_with_key,
    insert_job,
    job_row,
    mark_claimed,
    pending_review_payload,
    unknown_job_id,
)
from _submit_job_test_support import async_client as async_client  # noqa: F401
from _submit_job_test_support import conn as conn  # noqa: F401
from _submit_job_test_support import (
    isolate_async_session_local as isolate_async_session_local,  # noqa: F401
)
from _submit_job_test_support import isolated_schema as isolated_schema  # noqa: F401
from psycopg import Connection

pytestmark = DB_SKIP


async def _submit_pending_review(
    async_client: httpx.AsyncClient,
    *,
    key: str,
    job_id: object,
) -> dict[str, Any]:
    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=pending_review_payload(decisions_made=[], learnings=[]),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


@pytest.mark.asyncio
async def test_review_complete_done_allows_different_reviewer_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    claimant_id, claimant_key, _project_id, pipeline_id = fixture_project(conn)
    reviewer_id, reviewer_key = insert_actor_with_key(conn, name="job-test-reviewer")
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=_project_id,
        created_by_actor_id=claimant_id,
        title="needs review",
        contract=CONTRACT,
    )
    mark_claimed(conn, job_id, actor_id=claimant_id)
    await _submit_pending_review(async_client, key=claimant_key, job_id=job_id)
    assert job_row(conn, job_id)["state"] == "pending_review"

    response = await async_client.post(
        f"/jobs/{job_id}/review-complete",
        headers=auth_headers(reviewer_key),
        json={"final_outcome": "done", "notes": "review accepted"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job"]["id"] == str(job_id)
    assert payload["job"]["state"] == "done"
    row = job_row(conn, job_id)
    assert row["state"] == "done"
    assert row["claimed_by_actor_id"] is None
    assert row["claimed_at"] is None
    assert row["claim_heartbeat_at"] is None

    reviews = [row for row in audit_rows(conn) if row["op"] == "review_complete"]
    assert len(reviews) == 1
    audit = reviews[0]
    assert audit["target_kind"] == "job"
    assert audit["target_id"] == str(job_id)
    assert audit["error_code"] is None
    assert audit["request_payload"] == {
        "job_id": str(job_id),
        "final_outcome": "done",
        "notes": "review accepted",
    }
    assert audit["response_payload"] == {
        "final_outcome": "done",
        "prior_state": "pending_review",
    }

    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT authenticated_actor_id
            FROM audit_log
            WHERE op = 'review_complete' AND target_id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == reviewer_id
    assert row[0] != claimant_id


@pytest.mark.asyncio
async def test_review_complete_failed_succeeds_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    claimant_id, claimant_key, project_id, pipeline_id = fixture_project(conn)
    reviewer_id, reviewer_key = insert_actor_with_key(conn, name="job-test-reviewer")
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=claimant_id,
        title="review failed",
        contract=CONTRACT,
    )
    mark_claimed(conn, job_id, actor_id=claimant_id)
    await _submit_pending_review(async_client, key=claimant_key, job_id=job_id)

    response = await async_client.post(
        f"/jobs/{job_id}/review-complete",
        headers=auth_headers(reviewer_key),
        json={"final_outcome": "failed"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["job"]["state"] == "failed"
    reviews = [row for row in audit_rows(conn) if row["op"] == "review_complete"]
    assert len(reviews) == 1
    assert reviews[0]["error_code"] is None
    assert reviews[0]["response_payload"] == {
        "final_outcome": "failed",
        "prior_state": "pending_review",
    }
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT authenticated_actor_id
            FROM audit_log
            WHERE op = 'review_complete' AND target_id = %s
            """,
            (job_id,),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == reviewer_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    ["draft", "ready", "in_progress", "done", "failed", "blocked", "cancelled"],
)
async def test_review_complete_wrong_state_returns_409_with_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
    state: str,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        state=state,
        title=f"wrong state {state}",
    )
    if state == "in_progress":
        mark_claimed(conn, job_id, actor_id=actor_id)

    response = await async_client.post(
        f"/jobs/{job_id}/review-complete",
        headers=auth_headers(key),
        json={"final_outcome": "done"},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "job_not_pending_review"}
    assert job_row(conn, job_id)["state"] == state
    reviews = [row for row in audit_rows(conn) if row["op"] == "review_complete"]
    assert len(reviews) == 1
    assert reviews[0]["target_id"] == str(job_id)
    assert reviews[0]["error_code"] == "job_not_pending_review"


@pytest.mark.asyncio
async def test_review_complete_not_found_returns_404_with_audit(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id = fixture_project(conn)
    missing_job_id = unknown_job_id()

    response = await async_client.post(
        f"/jobs/{missing_job_id}/review-complete",
        headers=auth_headers(key),
        json={"final_outcome": "done"},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "job_not_found"}
    reviews = [row for row in audit_rows(conn) if row["op"] == "review_complete"]
    assert len(reviews) == 1
    assert reviews[0]["target_id"] == str(missing_job_id)
    assert reviews[0]["error_code"] == "job_not_found"


@pytest.mark.asyncio
async def test_review_complete_invalid_final_outcome_is_pydantic_422_not_audited(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        state="pending_review",
        title="invalid final outcome",
    )

    for final_outcome in [
        "pending_review",
        "blocked",
        "draft",
        "ready",
        "in_progress",
        "cancelled",
    ]:
        response = await async_client.post(
            f"/jobs/{job_id}/review-complete",
            headers=auth_headers(key),
            json={"final_outcome": final_outcome},
        )
        assert response.status_code == 422

    assert [row for row in audit_rows(conn) if row["op"] == "review_complete"] == []
