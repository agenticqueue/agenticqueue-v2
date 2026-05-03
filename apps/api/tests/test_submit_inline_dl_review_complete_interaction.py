import httpx
import pytest
from _submit_job_test_support import (
    DB_SKIP,
    audit_rows,
    auth_headers,
    claimed_job,
    decision_rows,
    job_row,
    learning_rows,
    pending_review_payload,
)
from _submit_job_test_support import (
    async_client as async_client,  # noqa: F401
)
from _submit_job_test_support import (
    conn as conn,  # noqa: F401
)
from _submit_job_test_support import (
    isolate_async_session_local as isolate_async_session_local,  # noqa: F401
)
from _submit_job_test_support import (
    isolated_schema as isolated_schema,  # noqa: F401
)
from aq_api.models import ReviewCompleteResponse, SubmitJobResponse
from psycopg import Connection

pytestmark = DB_SKIP


@pytest.mark.asyncio
async def test_pending_review_submit_creates_inline_dl_before_review_complete(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id, job_id = claimed_job(conn)

    submit_response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=pending_review_payload(),
    )

    assert submit_response.status_code == 200
    submit_body = SubmitJobResponse.model_validate(submit_response.json())
    assert submit_body.job.state == "pending_review"
    assert len(decision_rows(conn, job_id)) == 2
    assert len(learning_rows(conn, job_id)) == 2

    review_response = await async_client.post(
        f"/jobs/{job_id}/review-complete",
        headers=auth_headers(key),
        json={"final_outcome": "done", "notes": "review accepted"},
    )

    assert review_response.status_code == 200
    review_body = ReviewCompleteResponse.model_validate(review_response.json())
    assert review_body.job.state == "done"
    assert job_row(conn, job_id)["state"] == "done"
    assert len(decision_rows(conn, job_id)) == 2
    assert len(learning_rows(conn, job_id)) == 2
    assert [row["op"] for row in audit_rows(conn)] == [
        "submit_job",
        "review_complete",
    ]


@pytest.mark.asyncio
async def test_review_complete_rejects_inline_dl_fields_without_creating_rows(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id, job_id = claimed_job(conn)

    submit_response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=pending_review_payload(decisions_made=[], learnings=[]),
    )
    assert submit_response.status_code == 200
    before_audit_rows = audit_rows(conn)

    review_response = await async_client.post(
        f"/jobs/{job_id}/review-complete",
        headers=auth_headers(key),
        json={
            "final_outcome": "done",
            "notes": "review accepted",
            "decisions_made": [],
            "learnings": [],
        },
    )

    assert review_response.status_code == 422
    assert job_row(conn, job_id)["state"] == "pending_review"
    assert decision_rows(conn, job_id) == []
    assert learning_rows(conn, job_id) == []
    assert audit_rows(conn) == before_audit_rows
