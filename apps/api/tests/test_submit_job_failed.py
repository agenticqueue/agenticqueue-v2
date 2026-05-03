import httpx
import pytest
from _submit_job_test_support import (
    DB_SKIP,
    assert_claim_cleared,
    assert_inline_dl_created,
    audit_rows,
    auth_headers,
    claimed_job,
    decision_rows,
    failed_payload,
    job_row,
    learning_rows,
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
from aq_api.models import SubmitJobResponse
from psycopg import Connection

pytestmark = DB_SKIP


@pytest.mark.asyncio
async def test_submit_job_failed_accepts_empty_dod_results_and_audits(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    actor_id, key, _project_id, _pipeline_id, job_id = claimed_job(conn)
    payload = failed_payload()

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 200
    body = SubmitJobResponse.model_validate(response.json())
    assert body.job.state == "failed"
    assert body.created_gated_on_edge is False
    assert_claim_cleared(job_row(conn, job_id))
    assert_inline_dl_created(conn, job_id=job_id, actor_id=actor_id, response=body)
    rows = audit_rows(conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["op"] == "submit_job"
    assert row["target_kind"] == "job"
    assert row["target_id"] == str(job_id)
    assert row["request_payload"]["job_id"] == str(job_id)
    assert row["request_payload"]["outcome"] == "failed"
    assert row["request_payload"]["failure_reason"] == payload["failure_reason"]
    assert row["response_payload"] == {
        "outcome": "failed",
        "created_decisions": [str(value) for value in body.created_decisions],
        "created_learnings": [str(value) for value in body.created_learnings],
        "created_gated_on_edge": False,
    }
    assert row["error_code"] is None


@pytest.mark.asyncio
async def test_submit_job_failed_non_empty_dod_results_require_known_dod_ids(
    conn: Connection[tuple[object, ...]],
    async_client: httpx.AsyncClient,
) -> None:
    _actor_id, key, _project_id, _pipeline_id, job_id = claimed_job(conn)
    payload = failed_payload(
        dod_results=[
            {
                "dod_id": "unknown",
                "status": "failed",
                "evidence": [],
                "summary": "failed before known DoD",
            }
        ],
        decisions_made=[],
        learnings=[],
    )

    response = await async_client.post(
        f"/jobs/{job_id}/submit",
        headers=auth_headers(key),
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["error"] == "contract_violation"
    assert response.json()["details"]["rule"] == "dod_id_unknown"
    assert job_row(conn, job_id)["state"] == "in_progress"
    assert decision_rows(conn, job_id) == []
    assert learning_rows(conn, job_id) == []
    rows = audit_rows(conn)
    assert rows[0]["error_code"] == "contract_violation"
    assert rows[0]["response_payload"]["details"]["rule"] == "dod_id_unknown"
