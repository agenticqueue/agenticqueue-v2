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
    insert_job,
    job_row,
    mark_claimed,
)
from _submit_job_test_support import async_client as async_client  # noqa: F401
from _submit_job_test_support import conn as conn  # noqa: F401
from _submit_job_test_support import (
    isolate_async_session_local as isolate_async_session_local,  # noqa: F401
)
from _submit_job_test_support import isolated_schema as isolated_schema  # noqa: F401
from psycopg import Connection

pytestmark = DB_SKIP

JOB_STATES = [
    "draft",
    "ready",
    "in_progress",
    "done",
    "failed",
    "blocked",
    "pending_review",
    "cancelled",
]


def _done_payload() -> dict[str, Any]:
    return {
        "outcome": "done",
        "dod_results": [
            {
                "dod_id": "tests-pass",
                "status": "passed",
                "evidence": ["pytest -q apps/api/tests/test_state_machine.py"],
                "summary": "tests pass",
            },
            {
                "dod_id": "docs-reviewed",
                "status": "not_applicable",
                "evidence": [],
                "summary": "docs not touched",
            },
        ],
        "commands_run": ["pytest -q apps/api/tests/test_state_machine.py"],
        "verification_summary": "state matrix submit",
        "files_changed": ["apps/api/src/aq_api/services/review.py"],
        "risks_or_deviations": [],
        "handoff": "AQ2-79",
        "decisions_made": [],
        "learnings": [],
    }


@pytest.mark.asyncio
async def test_submit_and_review_complete_state_machine_matrix(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    observed: dict[str, dict[str, int]] = {}

    for state in JOB_STATES:
        job_id = insert_job(
            conn,
            pipeline_id=pipeline_id,
            project_id=project_id,
            created_by_actor_id=actor_id,
            state=state,
            title=f"submit matrix {state}",
            contract=CONTRACT,
        )
        if state == "in_progress":
            mark_claimed(conn, job_id, actor_id=actor_id)
        response = await async_client.post(
            f"/jobs/{job_id}/submit",
            headers=auth_headers(key),
            json=_done_payload(),
        )
        expected_status = 200 if state == "in_progress" else 409
        assert response.status_code == expected_status, (state, response.text)
        if state == "in_progress":
            assert response.json()["job"]["state"] == "done"
        else:
            assert response.json() == {"error": "job_not_in_progress"}
            assert job_row(conn, job_id)["state"] == state
        observed.setdefault(state, {})["submit_job"] = response.status_code

    for state in JOB_STATES:
        job_id = insert_job(
            conn,
            pipeline_id=pipeline_id,
            project_id=project_id,
            created_by_actor_id=actor_id,
            state=state,
            title=f"review matrix {state}",
            contract=CONTRACT,
        )
        if state == "in_progress":
            mark_claimed(conn, job_id, actor_id=actor_id)
        response = await async_client.post(
            f"/jobs/{job_id}/review-complete",
            headers=auth_headers(key),
            json={"final_outcome": "done"},
        )
        expected_status = 200 if state == "pending_review" else 409
        assert response.status_code == expected_status, (state, response.text)
        if state == "pending_review":
            assert response.json()["job"]["state"] == "done"
        else:
            assert response.json() == {"error": "job_not_pending_review"}
            assert job_row(conn, job_id)["state"] == state
        observed.setdefault(state, {})["review_complete"] = response.status_code

    assert set(observed) == set(JOB_STATES)
    assert all(
        set(result) == {"submit_job", "review_complete"}
        for result in observed.values()
    )


@pytest.mark.asyncio
async def test_cancel_job_still_allows_pending_review_and_blocked(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    job_ids = [
        insert_job(
            conn,
            pipeline_id=pipeline_id,
            project_id=project_id,
            created_by_actor_id=actor_id,
            state=state,
            title=f"cancel {state}",
            contract=CONTRACT,
        )
        for state in ("pending_review", "blocked")
    ]

    for job_id in job_ids:
        response = await async_client.post(
            f"/jobs/{job_id}/cancel",
            headers=auth_headers(key),
        )
        assert response.status_code == 200, response.text
        assert response.json()["job"]["state"] == "cancelled"
        assert job_row(conn, job_id)["state"] == "cancelled"

    cancel_rows = [row for row in audit_rows(conn) if row["op"] == "cancel_job"]
    assert len(cancel_rows) == 2
    assert all(row["error_code"] is None for row in cancel_rows)


@pytest.mark.asyncio
async def test_claim_next_job_skips_all_non_ready_cap05_states(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, project_id, pipeline_id = fixture_project(conn)
    ready_job_id = insert_job(
        conn,
        pipeline_id=pipeline_id,
        project_id=project_id,
        created_by_actor_id=actor_id,
        state="ready",
        title="only ready job",
        contract=CONTRACT,
    )
    for state in ("pending_review", "blocked", "done", "failed", "cancelled"):
        job_id = insert_job(
            conn,
            pipeline_id=pipeline_id,
            project_id=project_id,
            created_by_actor_id=actor_id,
            state=state,
            title=f"non-ready {state}",
            contract=CONTRACT,
        )
        if state == "in_progress":
            mark_claimed(conn, job_id, actor_id=actor_id)

    response = await async_client.post(
        "/jobs/claim",
        headers=auth_headers(key),
        json={"project_id": str(project_id)},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["job"]["id"] == str(ready_job_id)
    assert payload["job"]["state"] == "in_progress"
